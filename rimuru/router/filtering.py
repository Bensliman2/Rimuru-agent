"""Two-stage event filtering (article s3.1).

Once the agent is wired into real group chats and code platforms, the event
stream is noisy and the trigger rate is low: most messages need no action at
all. Having a large model judge every event from scratch would be far too slow
and expensive, so we borrow Fast Path / Slow Path:

    Fast Path (code)   deterministic admission checks on source, channel,
                       allowlists and duplicates. Runs in microseconds and
                       removes the bulk of the stream before any model is called.

    Slow Path (model)  whatever survives is judged semantically against policies
                       written as prompts. This stage doubles as a natural-language
                       extension point: a maintainer can add, disable or reword a
                       policy at runtime without touching code.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from ..llm.base import LLMClient, Msg
from ..models import (
    V_ACCEPTED,
    V_CANDIDATE,
    V_DROPPED_FAST,
    V_DROPPED_SLOW,
    Trigger,
)

MODE_IGNORE = "ignore"
MODE_MENTION_ONLY = "mention_only"
MODE_CANDIDATE = "candidate"


@dataclass
class ChannelPolicy:
    """Programmatic policy for one event source.

    mode:
      ignore         drop everything from this source
      mention_only   act only on an explicit request directed at the agent
      candidate      treat every event as a candidate for semantic judgement
    """

    mode: str = MODE_CANDIDATE
    allow_authors: set[str] = field(default_factory=set)   # empty = everyone
    deny_authors: set[str] = field(default_factory=set)
    allow_kinds: set[str] = field(default_factory=set)     # empty = all kinds
    mention_tokens: list[str] = field(default_factory=lambda: ["@rimuru", "rimuru"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "allow_authors": sorted(self.allow_authors),
            "deny_authors": sorted(self.deny_authors),
            "allow_kinds": sorted(self.allow_kinds),
            "mention_tokens": list(self.mention_tokens),
        }


DEFAULT_SEMANTIC_POLICIES: list[dict[str, Any]] = [
    {
        "id": "work_requests",
        "enabled": True,
        "text": "Act when someone reports a defect, a failure, a regression, or asks "
                "for a change, a review, or an investigation.",
    },
    {
        "id": "direct_questions",
        "enabled": True,
        "text": "Act when someone asks you a direct question, or asks for the status "
                "of work you are doing.",
    },
    {
        "id": "ci_failures",
        "enabled": True,
        "text": "Act on a failing CI run on a branch or pull request you are involved in. "
                "Ignore CI runs that passed.",
    },
    {
        "id": "silent_repro",
        "enabled": True,
        "text": "Speculative: when someone describes a bug in passing without asking "
                "you for anything, start reproducing and fixing it in the background. "
                "Only surface a result if you confirm the issue AND find a simple fix. "
                "Otherwise stay quiet.",
    },
    {
        "id": "no_chatter",
        "enabled": True,
        "text": "Never act on social chatter, acknowledgements, jokes, or logistics "
                "that do not concern you.",
    },
]


def parse_verdict(text: str | None) -> tuple[str, str]:
    """Read the semantic filter's answer. Only an explicit IGNORE drops an event.

    Found against a real reasoning model: when its thinking used up the token
    budget the answer came back empty, and treating "no clear answer" as
    "ignore" silently dropped a genuine bug report. Anything short of an
    explicit IGNORE now fails open - an extra look by the Router is cheap,
    a lost request is not.
    """
    for line in (text or "").splitlines():
        clean = line.strip().strip("*`_ ").lstrip("->").strip().strip("*`_ ")
        upper = clean.upper()
        if upper.startswith("IGNORE"):
            return V_DROPPED_SLOW, clean[6:].lstrip(":*- ").strip() or "no action needed"
        if upper.startswith("ACT"):
            return V_ACCEPTED, clean[3:].lstrip(":*- ").strip() or "needs handling"
    return V_ACCEPTED, "the filter gave no clear verdict, so admitting rather than risk a drop"


class EventFilter:
    def __init__(
        self,
        *,
        store: Any,
        bus: Any,
        config: Any,
        semantic_client: LLMClient | None = None,
        policies: dict[str, ChannelPolicy] | None = None,
    ) -> None:
        self.store = store
        self.bus = bus
        self.config = config
        self.semantic_client = semantic_client
        self.channel_policies: dict[str, ChannelPolicy] = policies or {}
        self.default_policy = ChannelPolicy()
        self.semantic_policies: list[dict[str, Any]] = [dict(p) for p in DEFAULT_SEMANTIC_POLICIES]
        self.agent_names = {"rimuru", "agent", "bot"}
        self._recent_content: dict[str, float] = {}
        # Wall-clock windows must shrink when the simulation compresses time,
        # or a 20s duplicate window would cover minutes of simulated chat.
        self.time_scale = 1.0
        self.dedupe_seconds = 20.0
        # Candidates are judged concurrently, but not without bound: a burst
        # should queue, not fan out into hundreds of simultaneous calls.
        self._slow_gate = asyncio.Semaphore(8)
        self.counters = {
            "seen": 0, "dropped_fast": 0, "candidates": 0,
            "dropped_slow": 0, "accepted": 0, "slow_calls": 0,
        }

    # ------------------------------------------------------------------ #
    # policy administration (exposed to the UI)
    # ------------------------------------------------------------------ #
    def policy_for(self, channel: str) -> ChannelPolicy:
        return self.channel_policies.get(channel, self.default_policy)

    def set_channel_mode(self, channel: str, mode: str) -> ChannelPolicy:
        policy = self.channel_policies.setdefault(channel, ChannelPolicy())
        policy.mode = mode
        self.bus.emit("policy.changed", channel=channel, mode=mode)
        return policy

    def set_semantic_policy(self, policy_id: str, *, enabled: bool | None = None,
                            text: str | None = None) -> dict[str, Any] | None:
        for policy in self.semantic_policies:
            if policy["id"] == policy_id:
                if enabled is not None:
                    policy["enabled"] = enabled
                if text is not None:
                    policy["text"] = text
                self.bus.emit("policy.changed", policy=policy)
                return policy
        return None

    def add_semantic_policy(self, text: str, policy_id: str | None = None) -> dict[str, Any]:
        policy = {
            "id": policy_id or f"custom_{len(self.semantic_policies)}",
            "enabled": True,
            "text": text,
        }
        self.semantic_policies.append(policy)
        self.bus.emit("policy.changed", policy=policy)
        return policy

    def describe(self) -> dict[str, Any]:
        return {
            "channels": {k: v.to_dict() for k, v in self.channel_policies.items()},
            "default": self.default_policy.to_dict(),
            "semantic": self.semantic_policies,
            "counters": dict(self.counters),
        }

    # ------------------------------------------------------------------ #
    # Fast Path: deterministic, no model
    # ------------------------------------------------------------------ #
    def fast_path(self, trigger: Trigger) -> tuple[str, str]:
        policy = self.policy_for(trigger.channel)
        content = (trigger.content or "").strip()
        low = content.lower()

        if not content:
            return V_DROPPED_FAST, "empty content"

        if trigger.kind == "human_direct":
            return V_CANDIDATE, "direct message to the agent"

        if policy.mode == MODE_IGNORE:
            return V_DROPPED_FAST, f"channel {trigger.channel} is set to ignore"

        author = (trigger.author or "").lstrip("@").lower()
        if author in self.agent_names:
            return V_DROPPED_FAST, "the agent's own message"
        if author in {a.lower() for a in policy.deny_authors}:
            return V_DROPPED_FAST, f"author {trigger.author} is denied"
        if policy.allow_authors and author not in {a.lower() for a in policy.allow_authors}:
            return V_DROPPED_FAST, f"author {trigger.author} is not on the allowlist"
        if policy.allow_kinds and trigger.kind not in policy.allow_kinds:
            return V_DROPPED_FAST, f"kind {trigger.kind} not admitted on this channel"

        if policy.mode == MODE_MENTION_ONLY:
            if not any(tok.lower() in low for tok in policy.mention_tokens):
                return V_DROPPED_FAST, "no explicit request directed at the agent"
            return V_CANDIDATE, "explicitly mentioned"

        # CI noise: a green run is not news.
        if trigger.kind == "ci":
            status = str(trigger.meta.get("status", "")).lower()
            if status in {"success", "passed", "green", "ok"}:
                return V_DROPPED_FAST, "CI run passed"
            return V_CANDIDATE, f"CI {status or 'result'} needs judgement"

        # Cheap duplicate suppression inside a short window.
        fingerprint = f"{trigger.channel}|{trigger.thread_key}|{low[:120]}"
        last_seen = self._recent_content.get(fingerprint)
        current = time.time()
        self._recent_content[fingerprint] = current
        if last_seen and current - last_seen < self.dedupe_seconds / self.time_scale:
            return V_DROPPED_FAST, "duplicate of a very recent event"
        if len(self._recent_content) > 4000:
            cutoff = current - 300
            self._recent_content = {
                k: v for k, v in self._recent_content.items() if v > cutoff
            }

        if len(content) < 3:
            return V_DROPPED_FAST, "too short to be actionable"

        return V_CANDIDATE, "passed programmatic checks"

    # ------------------------------------------------------------------ #
    # Slow Path: semantic judgement
    # ------------------------------------------------------------------ #
    def _semantic_prompt(self) -> str:
        active = [p for p in self.semantic_policies if p.get("enabled")]
        lines = [
            "You are the semantic admission filter for an autonomous agent that "
            "monitors real group chats, code review and CI.",
            "Decide whether one event deserves the agent's attention.",
            "",
            "Policies currently in force:",
        ]
        lines += [f"  - {p['text']}" for p in active]
        lines += [
            "",
            "Most events do not need action. Staying quiet is the correct and common answer.",
            'Reply with exactly one line: "ACT: <short reason>" or "IGNORE: <short reason>".',
        ]
        return "\n".join(lines)

    async def slow_path(self, trigger: Trigger) -> tuple[str, str]:
        if self.semantic_client is None:
            return V_ACCEPTED, "no semantic stage configured, admitting"

        thread_hint = ""
        if trigger.thread_id and (thread := self.store.threads.get(trigger.thread_id)):
            if thread.subscribers:
                thread_hint = (
                    f"\nA worker is already assigned to this thread "
                    f"({', '.join(thread.subscribers)})."
                )

        prompt = (
            f"Event on {trigger.channel} {trigger.thread_key}\n"
            f"from: {trigger.author}\n"
            f"kind: {trigger.kind}\n"
            f"content: {trigger.content[:1500]}{thread_hint}"
        )

        self.counters["slow_calls"] += 1
        try:
            async with self._slow_gate:
                response = await self.semantic_client.complete(
                    system=self._semantic_prompt(),
                    messages=[Msg(role="user", content=prompt)],
                    tools=[],
                    # Generous on purpose: reasoning models think before they
                    # answer, and a tight cap leaves them no room for the answer.
                    max_tokens=1024,
                    role="filter",
                )
        except Exception as exc:
            # Fail open: a filter outage must not make the agent deaf.
            return V_ACCEPTED, f"semantic stage unavailable ({type(exc).__name__}), admitting"

        return parse_verdict(response.text)

    # ------------------------------------------------------------------ #
    # staged entry points used by the EventRouter
    # ------------------------------------------------------------------ #
    def evaluate_fast(self, trigger: Trigger) -> tuple[str, str]:
        """Stage 1. Records the verdict and returns it. No model is called."""
        self.counters["seen"] += 1
        started = time.perf_counter()
        verdict, reason = self.fast_path(trigger)
        trigger.fast_path_ms = (time.perf_counter() - started) * 1000

        if verdict == V_DROPPED_FAST:
            self.counters["dropped_fast"] += 1
        else:
            self.counters["candidates"] += 1
            verdict = V_CANDIDATE

        trigger.verdict, trigger.verdict_reason = verdict, reason
        self.store.put_trigger(trigger)
        self.bus.emit(
            "filter.decided", trigger_id=trigger.id, stage="fast",
            verdict=verdict, reason=reason, ms=trigger.fast_path_ms,
        )
        return verdict, reason

    async def evaluate_semantic(self, trigger: Trigger) -> tuple[str, str]:
        """Stage 2. Only reached by events with no Worker already on them."""
        verdict, reason = await self.slow_path(trigger)
        trigger.verdict, trigger.verdict_reason = verdict, reason
        self.store.put_trigger(trigger)
        self.counters["accepted" if verdict == V_ACCEPTED else "dropped_slow"] += 1
        self.bus.emit(
            "filter.decided", trigger_id=trigger.id, stage="semantic",
            verdict=verdict, reason=reason,
        )
        return verdict, reason

    async def evaluate(self, trigger: Trigger) -> str:
        """Both stages back to back. Convenience for tests."""
        verdict, _ = self.evaluate_fast(trigger)
        if verdict == V_DROPPED_FAST:
            return verdict
        verdict, _ = await self.evaluate_semantic(trigger)
        return verdict
