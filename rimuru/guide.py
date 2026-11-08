"""The Guide (Under the hood -> Guide): ask about your sessions and clones.

"How many clones are working?", "What is each clone doing?", "Why did Dax
fail?" - scoped to a session and/or a clone the person picks.

The common questions are answered at once from the records - no AI call, so
they cost nothing and still work when the AI service is refusing. Anything else
gets a short answer from the AI, grounded only in the facts about what was
picked, including what the watchdog noticed. The watchdog itself stays a quiet
supervisor; the Guide only reads.
"""
from __future__ import annotations

import re
import time
from typing import Any

from .llm.base import Msg
from .sessions import DEFAULT, title_of
from .web import friendly

GUIDE_PROMPT = """You are Rimuru's Guide. The person asks about their sessions and
their clones (Rimuru's helpers, each doing one job). Answer from the facts below
only, in plain words, in a few sentences (at most about 120 words). Name clones by
their names. If the facts do not say, say you cannot tell from what you can see.
Never show internal IDs."""

_HOW_MANY = re.compile(r"\bhow many\b.*\b(clones?|workers?|helpers?|jobs?)\b", re.I)
_EACH = re.compile(r"\b(what|who)\b.*\b(each|every|all|the)\b.*\b(clones?|workers?)\b.*\b(doing|working on|up to)\b"
                   r"|\bwhat are (the )?(clones?|workers?) doing\b", re.I)
_STATUS = re.compile(r"\b(how is|how's)\b.*\b(going|session)\b|\bstatus\b|\bprogress\b", re.I)
_ONE = re.compile(r"\b(what|how)('s| is| has)?\b.*\b(doing|going|up to|done so far|status|progress)\b", re.I)

LIVE = {"working", "waiting"}
DOING = {"waiting": "waiting for a free spot", "done": "finished", "failed": "could not finish",
         "stopped": "stopped", "interrupted": "interrupted by a restart"}


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


class Guide:
    def __init__(self, system: Any) -> None:
        self.system = system

    # ------------------------------------------------------------------ #
    def _clones(self, session_key: str | None) -> list[Any]:
        store, conv = self.system.store, self.system.config.conversation_channels
        out = []
        for worker in store.sessions_of_kind("worker"):
            task = store.tasks.get(worker.task_id or "")
            thread = store.threads.get((task.thread_id if task else None) or "")
            if session_key and not (thread and thread.channel in conv and thread.key == session_key):
                continue
            out.append(worker)
        return sorted(out, key=lambda w: w.created_ts)

    def _card(self, worker: Any) -> dict[str, Any]:
        return friendly.worker_card(self.system.store, worker, self.system.config.conversation_channels)

    @staticmethod
    def _line(card: dict[str, Any]) -> str:
        doing = card["activity"] or card["stage"] if card["phase"] == "working" else DOING.get(card["phase"], card["phase"])
        return f'{card["name"]} ({card["persona"]}) - "{card["title"]}": {doing}, {_ago(card["elapsed"])}'

    def _finding(self, code: str) -> str:
        """The watchdog's findings, in words: clones by name, never by ID."""
        kind, _, sid = code.partition(":")
        worker = self.system.store.sessions.get(sid)
        name = getattr(worker, "name", "") or "a clone"
        return {
            "stalled": f"{name} seems stuck - no progress for a while",
            "looping": f"{name} seems to be repeating itself",
            "errors": f"{name}'s calls to the AI keep failing",
            "router_api_errors": "Rimuru's calls to the AI are failing",
            "router_backlog": "messages are piling up for Rimuru",
            "failed_sessions": "some clones could not finish",
            "long_running_tasks": "some jobs have been running for a long time",
        }.get(kind, kind.replace("_", " "))

    # ------------------------------------------------------------------ #
    def instant(self, question: str, session_key: str | None = None, clone_id: str | None = None) -> str | None:
        """Common questions, answered from the records with no AI call."""
        store = self.system.store
        worker = store.sessions.get(clone_id or "")
        if worker is not None and worker.kind == "worker" and _ONE.search(question) \
                and not _HOW_MANY.search(question):
            card = self._card(worker)
            steps = friendly.worker_timeline(store, worker)[-3:]
            out = [self._line(card) + "."]
            if card["result"]:
                out.append(f"It reported: {card['result'][:400]}")
            if steps:
                out.append("Its last steps:")
                out += [f"- {' / '.join(s['lines'])[:200]}" for s in steps]
            return "\n".join(out)

        cards = [self._card(w) for w in self._clones(session_key)]
        live = [c for c in cards if c["phase"] in LIVE]
        where = " in this session" if session_key else ""
        if _HOW_MANY.search(question):
            if not live:
                return (f"No clone is working{where} right now. {len(cards)} "
                        f"{'has' if len(cards) == 1 else 'have'} worked{where} so far."
                        if cards else f"No clone has worked{where} yet.")
            names = ", ".join(c["name"] for c in live)
            earlier = len(cards) - len(live)
            return (f"{len(live)} clone{'s are' if len(live) > 1 else ' is'} working{where} right now: {names}."
                    + (f" {earlier} more worked earlier." if earlier else ""))
        if _EACH.search(question):
            if not live:
                return f"No clone is working{where} right now."
            return "Right now:\n" + "\n".join(f"- {self._line(c)}" for c in live)
        if _STATUS.search(question) and session_key:
            if not cards:
                return "Nothing has been handed to a clone in this session yet."
            done = sum(c["phase"] == "done" for c in cards)
            return (f"{done} of {len(cards)} job{'s' if len(cards) > 1 else ''} done, {len(live)} in progress.\n"
                    + "\n".join(f"- {self._line(c)}" for c in cards[-8:]))
        return None

    # ------------------------------------------------------------------ #
    def facts(self, session_key: str | None = None, clone_id: str | None = None) -> str:
        """What the AI may answer from: the picked session and clone, plainly."""
        system, store = self.system, self.system.store
        lines: list[str] = []
        cards = [self._card(w) for w in self._clones(session_key)]
        live = [c for c in cards if c["phase"] in LIVE]
        lines.append(f"Clones working right now{' in this session' if session_key else ''}: {len(live)}.")
        if session_key:
            thread = next((t for t in store.threads.values()
                           if t.key == session_key and t.channel in system.config.conversation_channels), None)
            if thread is not None:
                lines.append(f'Session: "{title_of(store, thread)}".')
                said = [m for m in store.chat if m.get("chat", DEFAULT) == session_key
                        and m["role"] in {"user", "rimuru"}][-6:]
                if said:
                    lines.append("Its last messages:")
                    lines += [f"  {'the person' if m['role'] == 'user' else 'Rimuru'}: "
                              f"{' '.join(str(m['text']).split())[:240]}" for m in said]
        lines.append("Clones:")
        lines += [f"  - {self._line(c)}" + (f"; reported: {c['result'][:300]}" if c["result"] else "")
                  for c in cards[-12:]] or ["  (none)"]
        worker = store.sessions.get(clone_id or "")
        if worker is not None and worker.kind == "worker":
            card = self._card(worker)
            task = store.tasks.get(worker.task_id or "")
            lines.append(f'\nAbout {card["name"]} ({card["persona"]}: {card["persona_line"]}):')
            lines.append(f'  Its job: "{card["title"]}". It was asked: '
                         f'{friendly.plain(task.brief)[:600] if task else "(unknown)"}')
            lines.append(f'  Now: {self._line(card)}. Files changed: '
                         f'{", ".join(card["files"][:10]) or "none"}. Tests: {card["tests"] or "none run"}.')
            if card["skills"]:
                lines.append(f"  Skills of the person's it read: {', '.join(card['skills'])}.")
            if card["result"]:
                lines.append(f"  It reported: {card['result'][:800]}")
            steps = friendly.worker_timeline(store, worker)[-12:]
            lines.append("  Its last steps:")
            lines += [f"    - {' / '.join(s['lines'])[:220]}" for s in steps] or ["    (none)"]
            notes = [e for e in system.bus.history
                     if e.get("session_id") == worker.id and str(e.get("topic", "")).startswith("watchdog.")][-5:]
            lines += [f"  The watchdog {e['topic'].split('.')[-1]}: {str(e.get('text', ''))[:200]}" for e in notes]
        findings = [self._finding(f) for f in system.watchdog.health_report()["findings"]]
        lines.append(f"\nThe watchdog's latest check: {'; '.join(findings) if findings else 'nothing wrong'}.")
        return "\n".join(lines)

    async def answer(self, question: Any, session_key: str | None = None,
                     clone_id: str | None = None) -> dict[str, Any]:
        question = " ".join(str(question or "").split())[:800]
        if not question:
            return {"answer": "Ask me something about your sessions or clones.", "instant": True}
        quick = self.instant(question, session_key, clone_id)
        if quick is not None:
            return {"answer": quick, "instant": True}
        if self.system.config.is_demo():
            return {"answer": "No AI model is set up (demo mode), so I can only answer the quick "
                              "questions - how many clones, what each one is doing, how a session "
                              "is going. Choose a model to ask me anything.", "instant": False, "demo": True}
        started = time.perf_counter()
        response = await self.system.watchdog.loop.client.complete(
            system=GUIDE_PROMPT,
            messages=[Msg("user", f"FACTS\n{self.facts(session_key, clone_id)}\n\nQUESTION\n{question}")],
            tools=[], max_tokens=700, role="watchdog")
        text = (response.text or "").strip() or "I cannot tell from what I can see."
        return {"answer": text, "instant": False, "seconds": round(time.perf_counter() - started, 1)}
