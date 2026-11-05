"""An offline stand-in for a real model.

This exists so the whole architecture can be run, watched and load-tested with
no API key and no network. It is a rule-based policy, not a model: it reads the
injected context, picks plausible tool calls, and reports simulated token usage.
One client instance is built per session, so it can carry a little per-session
state (whether this worker has wandered off, whether a question is pending).

Two things are simulated on purpose, so that each module has something real to
do:

  Latency. Router latency grows with the number of events merged into a call,
  but much more slowly than the batch does - the same shape the article reports
  in s7.2 (2.5s at batch 1 rising to only 3.9s at batch 2.8), scaled down to keep
  demos brisk. Everything the dashboard shows about batching on the mock is an
  illustration of that mechanism, NOT a measurement of a model.

  Drift. About one worker in eight wanders off: it stays alive and keeps
  stepping, but only re-reads its context instead of working. That is exactly the
  failure mode the Watchdog (s6) exists for - a liveness probe would call it
  healthy - and the worker recovers when the Watchdog nudges it.
"""
from __future__ import annotations

import asyncio
import random
import re
import secrets
from typing import Any

from ..models import Usage
from .base import LLMClient, LLMResponse, Msg, ToolCall, ToolSpec

TRIGGER_RE = re.compile(r"\[(trg_[0-9a-f]+)\]\s*\(([^)]*)\)\s*([^:\n]*):\s*(.*)")
THREAD_RE = re.compile(r"thread:\s*(thr_[0-9a-f]+)", re.I)
TASKS_ON_RE = re.compile(r"tasks on this thread:\s*([^|\n]*)")
TASK_STATUS_RE = re.compile(r"(tsk_[0-9a-f]+)\s*\((\w+)[^)]*\)")
REPORT_RE = re.compile(
    r'Worker \[(wrk_[0-9a-f]+)\] on task \[(tsk_[0-9a-f]+)\] "(.*?)" reports status=(\w+)'
)
FINISH_RE = re.compile(r'Task \[(tsk_[0-9a-f]+)\] "(.*?)" finished as (\w+)\.')
TASK_ID_RE = re.compile(r"Your task is (tsk_[0-9a-f]+)")
TASK_TITLE_RE = re.compile(r'Your task is tsk_[0-9a-f]+: "(.*?)"')
WORKER_FLAG_RE = re.compile(r"^\[(wrk_[0-9a-f]+)\].*?\b(STALLED|LOOPING|API_ERRORS)\b", re.M)
LIST_STATUS_RE = re.compile(r"status=(\w+)")

WORK_WORDS = (
    "crash", "crashes", "broken", "break", "error", "fails", "failing", "failed",
    "bug", "regression", "traceback", "exception", "stack trace", "500",
    "fix", "implement", "add support", "please add", "can you add", "could you",
    "repro", "reproduce", "investigate", "look into", "can you look", "review", "patch",
    "timeout", "leak", "hangs", "stuck", "wrong output", "not working", "flaky",
    "build a", "build me", "write a", "write me", "make a", "make me", "create a",
)
CANCEL_WORDS = ("nevermind", "never mind", "cancel that", "disregard", "forget it",
                "no longer needed", "expected behaviour", "worked around it")
# Deliberately no bare "?": plenty of chatter is phrased as a question
# ("lunch?", "who's on call?") and none of it needs the agent.
STATUS_WORDS = ("any update", "where are we with", "status?", "how long",
                "is anyone looking", "eta")
# Something is wrong but nobody is asking the agent for anything. This is the
# signal speculative execution (s3.4) is meant to act on.
PASSING_HINTS = ("did something weird", "off-by-one", "full of warnings",
                 "something's up", "saw the same request")
URGENT_WORDS = ("blocking", "urgent", "release")
# Words that mark a message as a reply to something already under way.
REPLY_MARKERS = ("it ", "this", "that", "actually", "adding", "also", "one more", "locally")


def _classify(content: str) -> str:
    low = (content or "").lower().strip()
    if any(w in low for w in CANCEL_WORDS):
        return "cancel"
    if any(w in low for w in WORK_WORDS):
        return "work"
    if any(w in low for w in STATUS_WORDS):
        return "status"
    if any(w in low for w in PASSING_HINTS):
        return "speculate"
    return "ignore"


def _call(name: str, **args: Any) -> ToolCall:
    return ToolCall(id=f"mock_{secrets.token_hex(4)}", name=name, args=args)


def _is_tool_turn(msg: Msg) -> bool:
    return isinstance(msg.content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in msg.content
    )


def _meta(lines: list[str], start: int) -> tuple[dict[str, str], str]:
    """Read the 'key: value' lines after a header, then the paragraph after them."""
    meta: dict[str, str] = {}
    i = start + 1
    while i < len(lines) and lines[i].strip():
        key, sep, value = lines[i].partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    body: list[str] = []
    while i < len(lines) and lines[i].strip():
        body.append(lines[i].strip())
        i += 1
    return meta, " ".join(body)


def _significant(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 5}


class MockClient(LLMClient):
    """Rule-based policy shaped like an LLM. One instance per session."""

    name = "mock"

    def __init__(self, speed: float = 1.0, seed: int | None = None) -> None:
        self.speed = max(speed, 0.001)
        self.rng = random.Random(seed)
        # per-session state
        self.drift: bool | None = None
        self.drift_resolved = False
        self.pending_console_status = False
        self.pending_question: str | None = None

    # ------------------------------------------------------------------ #
    def _latency(self, role: str, merged: int) -> float:
        jitter = self.rng.uniform(0.0, 0.2)
        if role == "router":
            return 1.0 + 0.30 * max(0, merged - 1) + jitter
        if role == "filter":
            return 0.35 + jitter / 2
        return 0.8 + jitter

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec],
        max_tokens: int = 2048,
        role: str = "worker",
    ) -> LLMResponse:
        last = messages[-1].as_text() if messages else ""
        merged = max(1, len(TRIGGER_RE.findall(last)))
        await asyncio.sleep(self._latency(role, merged) / self.speed)

        context_chars = sum(len(m.as_text()) for m in messages) + len(system)
        usage = Usage(
            input_tokens=context_chars // 4,
            output_tokens=40 + 25 * merged,
            # On a long-lived session everything but the newest turn is a cache hit.
            cache_read_tokens=max(0, (context_chars // 4) - 400),
        )

        names = {t.name for t in tools}
        if role == "router":
            resp = self._router(last, names)
        elif role == "watchdog":
            resp = self._watchdog(last, messages, names)
        elif role == "filter":
            resp = self._filter(last)
        else:
            resp = self._worker(last, messages, names)
        resp.usage = usage
        if resp.tool_calls:
            resp.stop_reason = "tool_use"
        return resp

    # ------------------------------------------------------------------ #
    # Router: interacts and dispatches. It never does the work itself.
    # ------------------------------------------------------------------ #
    def _router(self, last: str, names: set[str]) -> LLMResponse:
        calls: list[ToolCall] = []
        notes: list[str] = []
        lines = last.splitlines()

        # A list_tasks result we asked for on behalf of the console.
        if self.pending_console_status and "status=" in last:
            self.pending_console_status = False
            counts: dict[str, int] = {}
            for status in LIST_STATUS_RE.findall(last):
                counts[status] = counts.get(status, 0) + 1
            summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing yet"
            calls.append(_call("reply", thread_id="console", text=f"Tasks right now: {summary}."))
            notes.append("answered the console")

        for i, line in enumerate(lines):
            if m := TRIGGER_RE.search(line):
                detail = lines[i + 1] if i + 1 < len(lines) else ""
                self._route_trigger(m, detail, names, calls, notes,
                                    handed_back="handed back" in last.lower())
            elif m := REPORT_RE.search(line):
                meta, body = _meta(lines, i)
                self._on_report(m, meta, body, calls, notes)
            elif m := FINISH_RE.search(line):
                meta, body = _meta(lines, i)
                speculative = i > 0 and "did not announce" in lines[i - 1].lower()
                self._on_finish(m, meta, speculative, calls, notes)
            elif "[watchdog escalation]" in line.lower():
                calls.append(_call(
                    "reply", thread_id="console",
                    text="Watchdog escalation: " + line.split("]", 1)[-1].strip()[:160],
                ))
                notes.append("relayed a watchdog escalation")

        calls = [c for c in calls if c.name in names]
        merged = len(TRIGGER_RE.findall(last))
        return LLMResponse(
            text="; ".join(notes) or "nothing actionable",
            thinking=(f"{merged} events merged into this turn; one decision each."
                      if merged > 1 else ""),
            tool_calls=calls,
        )

    def _route_trigger(self, m: re.Match, detail: str, names: set[str],
                       calls: list[ToolCall], notes: list[str], *, handed_back: bool) -> None:
        trig_id, source, author, content = m.groups()
        content = content.strip().strip('"')
        thread = (THREAD_RE.search(detail) or [None, ""])[1]
        statuses = TASK_STATUS_RE.findall((TASKS_ON_RE.search(detail) or [None, ""])[1])
        console = source.strip().startswith("console")
        intent = _classify(content)

        if intent == "cancel":
            if thread:
                calls.append(_call("stop_task", thread_id=thread,
                                   reason="the requester withdrew it"))
            notes.append(f"{trig_id}: cancellation")
            return

        if intent in {"work", "speculate"}:
            speculative = intent == "speculate" and not handed_back and not console
            title = content if len(content) <= 70 else content[:70].rsplit(" ", 1)[0]
            calls.append(_call(
                "dispatch_task",
                title=title.rstrip(" -,:;"),
                brief=(f"Requested in the chat: {content}" if console
                       else f"From {author.strip()} in {source.strip()}: {content}"),
                thread_id=thread,
                speculative="true" if speculative else "false",
            ))
            if console:
                calls.append(_call("reply", thread_id="console",
                                   text="On it - I've started a clone on this. "
                                        "I'll tell you here when it's done."))
            notes.append(f"{trig_id}: dispatched" + (" speculatively" if speculative else ""))
            return

        if intent == "status":
            if console:
                calls.append(_call("list_tasks"))
                self.pending_console_status = True
            elif any(s in {"running", "queued"} for _, s in statuses):
                calls.append(_call("reply", thread_id=thread,
                                   text="Still on it. I will post here once the fix is verified."))
            elif any(s == "done" for _, s in statuses):
                calls.append(_call("reply", thread_id=thread,
                                   text="That one is done and verified - details are above."))
            else:
                calls.append(_call("reply", thread_id=thread,
                                   text="Nothing is running on this right now. Want me to pick it up?"))
            notes.append(f"{trig_id}: status answered from inlined task state")
            return

        if console:
            calls.append(_call(
                "reply", thread_id="console",
                text="I am here. On the offline mock model I can only dispatch work and "
                     "report status - set ANTHROPIC_API_KEY to talk to me properly.",
            ))
            notes.append(f"{trig_id}: console chat")
            return

        calls.append(_call("ignore", trigger_id=trig_id, reason="no action needed"))
        notes.append(f"{trig_id}: ignored")

    def _on_report(self, m: re.Match, meta: dict[str, str], body: str,
                   calls: list[ToolCall], notes: list[str]) -> None:
        worker, task, title, status = m.groups()
        thread = meta.get("thread", "")
        if not thread.startswith("thr_"):
            thread = "console"
        if "asked for status" in body.lower():
            update = body.split(":", 1)[-1].strip()[:90]
            calls.append(_call("reply", thread_id=thread, text=f"Still on it: {update}"))
            notes.append(f"{task}: relayed status")
        elif status in {"blocked", "needs_decision"}:
            calls.append(_call("reply", thread_id=thread, text=f"Need a decision: {body[:120]}"))
            notes.append(f"{task}: asked for a decision")
        else:
            notes.append(f"{task}: routine progress, said nothing")

    def _on_finish(self, m: re.Match, meta: dict[str, str], speculative: bool,
                   calls: list[ToolCall], notes: list[str]) -> None:
        task, title, outcome = m.groups()
        thread = meta.get("thread", "")
        if not thread.startswith("thr_"):
            thread = "console"
        channel = meta.get("channel", "")

        if channel.startswith("console"):
            # The chat app is for people who do not use agents. In demo mode,
            # never let them believe something real was built.
            text = (f'Finished "{title}" - but this is demo mode, so nothing was '
                    f"actually built. Connect a real model to get real results.")
        elif outcome == "done":
            if speculative:
                text = (f'Heads up: while this was being discussed I reproduced it in the '
                        f'background and have a verified fix for "{title}". Want me to open a PR?')
            elif channel.startswith("github"):
                text = (f"**Summary**\nFixed: {title}.\n\n**What changed**\nRoot cause "
                        f"isolated against a reproduction, minimal patch applied.\n\n"
                        f"**Verification**\nThe reproduction now passes, as does the full suite.")
            else:
                text = f"Done: {title}. Reproduced it, fixed it, and the tests pass."
        else:
            text = (f'I could not get "{title}" to reproduce reliably, so there is no fix yet. '
                    f"Can someone share exact steps?")
        calls.append(_call("reply", thread_id=thread, text=text))
        notes.append(f"{task}: reported {outcome}")

    # ------------------------------------------------------------------ #
    # Worker: isolated context, full tools, one task.
    # ------------------------------------------------------------------ #
    def _worker(self, last: str, messages: list[Msg], names: set[str]) -> LLMResponse:
        low = last.lower()
        first = messages[0].as_text() if messages else ""
        my_task = (TASK_ID_RE.search(first) or [None, ""])[1]
        my_title = (TASK_TITLE_RE.search(first) or [None, ""])[1]
        if self.drift is None:
            self.drift = self.rng.random() < 0.12

        jobs_done = sum(1 for m in messages
                        if m.role == "user" and "job finished" in m.as_text().lower())
        jobs_failed = sum(1 for m in messages
                          if m.role == "user" and "job failed" in m.as_text().lower())

        def reply(text: str, *calls: ToolCall, thinking: str = "") -> LLMResponse:
            return LLMResponse(text=text, thinking=thinking,
                               tool_calls=[c for c in calls if c.name in names])

        if "has stopped this task" in low:
            return reply("Standing down.",
                         _call("finish", summary="Stopped by the Router.", outcome="cancelled"))

        # Several events can be merged into one turn (follow-ups that queued up
        # while this worker was thinking, or waiting for a slot). Read them all
        # first, so the main action and the side effects go out together.
        events: list[tuple[str, str]] = []
        if "new event on" in low:
            for m in TRIGGER_RE.finditer(last):
                events.append((m.group(1), m.group(4).strip().strip('"')))
        if any(_classify(content) == "cancel" for _, content in events):
            return reply("The request was withdrawn. Standing down.",
                         _call("finish", summary="Cancelled by the requester.",
                               outcome="cancelled"))

        side: list[ToolCall] = []
        reported = False
        for trig_id, content in events:
            intent = _classify(content)
            if intent == "status" and not reported:
                side.append(_call("report", status="progressing",
                                  text="Asked for status: reproduced it, and the fix is "
                                       "in and being verified."))
                reported = True
            elif intent == "work" and not any(k in content.lower() for k in REPLY_MARKERS) \
                    and not (_significant(content) & _significant(my_title)):
                side.append(_call("handback", trigger_id=trig_id,
                                  reason="a separate request, not part of my task"))
            elif any(w in content.lower() for w in URGENT_WORDS) and not reported:
                side.append(_call("report", status="progressing",
                                  text="The requester says this is blocking a release. "
                                       "Still on it."))
                reported = True

        if "[watchdog]" in low:
            if self.drift and not self.drift_resolved:
                self.drift_resolved = True
                return reply(
                    "Fair point - I was re-reading context instead of working. Starting now.",
                    _call("start", command="repro-case && collect-logs", label="reproduce"),
                    *side,
                    thinking="The watchdog is right; I have produced nothing.",
                )
            if not reported:
                side.append(_call("report", status="progressing",
                                  text="Fix applied, verifying now."))
            return reply("Still working.", *side)

        # Alive, stepping, burning tokens, getting nowhere: the s6 failure mode.
        if self.drift and not self.drift_resolved and not (jobs_done or jobs_failed) and my_task:
            return reply("Re-reading the task before I start.",
                         _call("recall", id=my_task),
                         thinking="I want to be sure of the scope first.")

        if "job finished" in low or "job failed" in low:
            if "job failed" in low and jobs_failed <= 1:
                return reply("That run failed. Retrying once with more logging.",
                             _call("start", command="repro-case --verbose",
                                   label="retry with logging"), *side,
                             thinking="One failure is not conclusive.")
            if jobs_failed >= 2:
                return reply("I cannot get a clean run.", _call(
                    "finish",
                    summary="Simulated: could not get a reliable run (demo mode only).",
                    outcome="failed"))
            if jobs_done >= 2:
                return reply("Fix applied and the suite passes.", _call(
                    "finish",
                    summary="Simulated: reproduced, fixed and verified - in demo mode only, "
                            "so nothing was really changed.",
                    outcome="done"),
                    thinking="Both stages are green, so the task is complete.")
            return reply("Reproduced. Applying a fix and re-running the suite.",
                         _call("start", command="apply-fix && run-tests",
                               label="fix and verify"), *side,
                         thinking="Stage one is done.")

        if "your task is" in low:
            return reply("Starting by reproducing the reported behaviour.",
                         _call("start", command="repro-case && collect-logs",
                               label="reproduce"), *side,
                         thinking="Reproduce first, so the fix can be verified against it.")

        if side:
            return reply("Handling the follow-up(s) that came in.", *side)

        # start() has just acknowledged. The whole point of the asynchronous loop
        # is that we do NOT spin here: yield the turn and wait for the event.
        if "it is running in the background" in low:
            return reply("Job is running. Waiting for the result instead of blocking.",
                         thinking="start() returned at once; the result arrives as an event.")

        if events:
            return reply("Noted the follow-up. It does not change the plan.")
        if "relayed by the router" in low:
            return reply("Understood, folding that in.")
        return reply("Nothing to act on in that; waiting.")

    # ------------------------------------------------------------------ #
    # Watchdog: judges progress, not just liveness.
    # ------------------------------------------------------------------ #
    def _watchdog(self, last: str, messages: list[Msg], names: set[str]) -> LLMResponse:
        low = last.lower()

        if "a maintainer asks" in low:
            self.pending_question = last.split("asks:", 1)[-1].strip().split("\n")[0]
            return LLMResponse(text="Checking current health before answering.",
                               tool_calls=[_call("health")])

        if messages and _is_tool_turn(messages[-1]):
            if self.pending_question and "=== health report ===" in last:
                question, self.pending_question = self.pending_question, None
                live = len(re.findall(r"^\[wrk_", last, re.M))
                flags = WORKER_FLAG_RE.findall(last)
                failed = re.search(r"failed sessions: (.*)", last)
                parts = [f"{live} worker(s) live."]
                parts.append(
                    "Flagged: " + ", ".join(f"{w} {f.lower()}" for w, f in flags) + "."
                    if flags else "None flagged as stalled or looping."
                )
                if failed:
                    parts.append(f"Failed sessions: {failed.group(1)}.")
                parts.append("(Offline mock: this is read straight off the health report; "
                             "a real model would investigate with recall().)")
                return LLMResponse(text="Answered.",
                                   tool_calls=[_call("answer", text=" ".join(parts))])
            return LLMResponse(text="Done for this tick.")

        calls = [
            _call("nudge", session_id=wid, text={
                "LOOPING": "You have repeated the same call for several turns without "
                           "progress. Stop re-reading and take your next concrete step.",
                "STALLED": "No step from you in a while and no job running. "
                           "What are you waiting for?",
                "API_ERRORS": "You are hitting API errors. Back off and retry.",
            }[flag])
            for wid, flag in WORKER_FLAG_RE.findall(last)
        ]
        if calls:
            return LLMResponse(
                text=f"{len(calls)} session(s) alive but not making progress; nudging.",
                thinking="Liveness alone would call these healthy.",
                tool_calls=[c for c in calls if c.name in names],
            )
        if "health report" in low:
            verdict = "degraded" if "failed sessions" in low else "healthy"
            return LLMResponse(text="Looked over the system.", tool_calls=[_call(
                "assess", verdict=verdict,
                note="all workers progressing" if verdict == "healthy"
                else "some sessions failed earlier",
            )])
        return LLMResponse(text="Nothing to do.")

    # ------------------------------------------------------------------ #
    # Semantic filter (Slow Path of s3.1)
    # ------------------------------------------------------------------ #
    def _filter(self, last: str) -> LLMResponse:
        low = last.lower()
        if "kind: human_direct" in low:
            return LLMResponse(text="ACT: addressed to the agent directly")
        if "kind: ci" in low:
            # Green builds never get this far, so a CI event here is a failure.
            return LLMResponse(text="ACT: a failing build needs attention")
        content = last.split("content:", 1)[-1].split("\n")[0]
        return LLMResponse(text={
            "work": "ACT: a defect or change request",
            "status": "ACT: someone is asking where the work stands",
            "cancel": "ACT: a request is being withdrawn",
            "speculate": "ACT: a problem mentioned in passing, worth reproducing quietly",
            "ignore": "IGNORE: chatter, nothing addressed to the agent",
        }[_classify(content)])
