"""The asynchronous ReAct loop (article s4.1).

Two things make this different from a classic ReAct loop:

1. Full duplex. Time-consuming work is launched with start(), which returns
   "Started" at once; the result arrives later as an injected event. The session
   therefore never blocks, and can take a new instruction while earlier work is
   still running.

2. Batching. Events that arrive while an inference turn is in flight queue up
   and are merged into a single injected message for the next turn. Because that
   turn is one long input and one short output, the merged triggers are prefilled
   in parallel, so time per request grows much more slowly than batch size - the
   effect measured in article s7.2. This is what lets a single long-lived Router
   session absorb bursts without sharding.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..config import Config
from ..context import trim_messages
from ..ids import new_id
from ..llm.base import LLMClient, Msg, ProviderAccountError
from ..logs import noted
from ..models import Session, Step, Usage, now
from ..safety import scrubbed
from .tools import ToolRegistry

log = logging.getLogger("rimuru.agent")


@dataclass
class Injection:
    """One thing waiting to enter a session's context."""

    text: str
    kind: str = "event"              # event | job | human | system | nudge
    trigger_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    uid: str = field(default_factory=lambda: os.urandom(6).hex())


class AgentLoop:
    def __init__(
        self,
        *,
        session: Session,
        client: LLMClient,
        store: Any,
        bus: Any,
        config: Config,
        system_prompt: str,
        registry: ToolRegistry,
        role: str,
        budget_chars: int,
        max_steps: int = 0,
        max_tokens: int = 2048,
    ) -> None:
        self.session = session
        self.client = client
        self.store = store
        self.bus = bus
        self.config = config
        self.system_prompt = system_prompt
        self.registry = registry
        self.role = role
        self.budget_chars = budget_chars
        self.max_steps = max_steps
        self.max_tokens = max_tokens

        self.messages: list[Msg] = []
        self.inbox: asyncio.Queue[Injection] = asyncio.Queue()
        self.last_activity_ts: float = now()
        self.last_step_ts: float = now()
        self.turn_started_ts: float = 0.0
        self.error_count = 0
        self.spilled_turns = 0
        self._truncations = 0

        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()

        # Cut-in (the Router sets this): a new message from a person may stop a
        # model call that something else started - a clone's report, a tool
        # result - so the person is not left waiting behind it. A turn that a
        # person started is never cut short.
        self.cut_in: Callable[[Injection], bool] | None = None
        # Set while the provider refuses this account; the turn is retried.
        self.blocked: ProviderAccountError | None = None
        self._blocked_wait = float(getattr(config, "blocked_retry_seconds", 30.0))
        self._retry_turn = False
        self.cut_ins = 0
        # What cut-ins threw away: calls abandoned, output already written, time.
        self.cut_in_waste: dict[str, Any] = {"calls": 0, "chars": 0, "seconds": 0.0,
                                             "streamed": False}
        self._call: asyncio.Future | None = None
        self._turn_from_person = False
        # The turn in progress: every event it answers (including ones folded in
        # on the way) and when it began. on_turn_end runs when the model stops
        # calling tools, with its final text - the Router uses it to catch an
        # answer that was written but never sent.
        self.turn_events: list[Injection] = []
        self._notes: list[str] = []
        self.turn_opened_ts = 0.0
        self.on_turn_end: Callable[[str], Awaitable[None]] | None = None
        # The live conversation is saved after every step (store.save_context), so
        # a restart resumes exactly where it stopped - see restore().
        self.persist = True
        self._restored_pending: list[dict[str, Any]] | None = None
        self._resume_input = ""
        # Extra context for a new turn, from the events in it (the Router: what it
        # remembers that may matter - rimuru/memory.py).
        self.annotate: Callable[[list[Injection]], str] | None = None
        # Tools that finish what a turn set out to do: a reply sent, a job
        # started, a verdict recorded. When every call in a step was one of them
        # and it worked, the model is not asked again just to read "done" - the
        # results go in with whatever wakes it next. Found in real use: 13 of
        # Rimuru's 31 calls only read "sent", and the watchdog called itself 87
        # times in a row to say "healthy".
        self.ends_turn: Callable[[list[tuple[str, str]]], bool] | None = None
        self.max_rounds = 0             # rounds of tool calls per turn (0: no limit)
        self._deferred: list[dict[str, Any]] | None = None
        # A model call failed for good (after the client's own retries):
        # (error, whether it is tried again). The Router tells whoever waits.
        self.on_call_failed: Callable[[Exception, bool], Awaitable[None]] | None = None
        # The client is retrying a call (too many requests, a server error): the
        # Router tells whoever waits, instead of leaving them in silence.
        self.on_struggling: Callable[[str], None] | None = None
        self.retry_base = 2.0           # seconds before the first retry of a failed turn
        # The inbox, saved: what waits for this session survives a restart.
        self._queued: list[dict[str, Any]] = []
        self._taken: set[str] = set()

    # ------------------------------------------------------------------ #
    # saved conversations: resuming exactly where a restart cut in
    # ------------------------------------------------------------------ #
    def _save(self, pending: list[dict[str, Any]] | None, *, deferred: bool = False) -> None:
        if not self.persist:
            return
        try:
            self.store.save_context(self.session.id, {
                "messages": [{"role": m.role, "content": m.content} for m in self.messages],
                "pending": pending,
                "deferred": deferred,
            })
        except Exception as exc:  # noqa: BLE001 - losing a save must not stop the work
            self.bus.emit("session.error", session_id=self.session.id, consecutive=0,
                          error=f"could not save the conversation: {type(exc).__name__}: {exc}")
            return
        if self._taken:
            # Taken in and now in the saved conversation: no longer waiting.
            self._queued = [item for item in self._queued if item["uid"] not in self._taken]
            self._taken = set()
            self._keep_inbox()

    def _keep_inbox(self) -> None:
        if self.persist:
            self.store.save_inbox(self.session.id, self._queued)

    def restore(self, saved: dict[str, Any]) -> None:
        """Take up a saved conversation exactly where it stopped.

        Three ways a restart can cut in: after tools ran but before the model saw
        their results (they are sent now); while tools were running (each gets a
        result saying it was cut off - the model checks before relying on it); or
        while the model was thinking (it is asked again)."""
        self.messages = [Msg(role=m["role"], content=m["content"]) for m in saved.get("messages") or []]
        self._notes = self.store.load_notes(self.session.id)
        last = self.messages[-1] if self.messages else None
        calls = ([b for b in last.content if isinstance(b, dict) and b.get("type") == "tool_use"]
                 if last is not None and last.role == "assistant" and isinstance(last.content, list)
                 else [])
        if saved.get("pending") and saved.get("deferred"):
            self._deferred = list(saved["pending"])      # it had finished: results go in next time
        elif saved.get("pending"):
            self._restored_pending = list(saved["pending"])
        elif calls:
            self._restored_pending = [{
                "type": "tool_result", "tool_use_id": b.get("id", ""),
                "content": ("(Cut off: the system restarted while this was running. It may or "
                            "may not have taken effect - check before relying on it.)"),
            } for b in calls]
        elif last is not None and last.role == "user":
            self._retry_turn = True                    # never answered: think again
            self._resume_input = last.as_text()
        # What was waiting for it, not yet taken in.
        for item in self.store.load_inbox(self.session.id):
            self._queued.append(item)
            self.inbox.put_nowait(Injection(text=item["text"], kind=item.get("kind", "event"),
                                            trigger_id=item.get("trigger_id"),
                                            meta=item.get("meta") or {}, uid=item["uid"]))

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            # The idle clock starts when the loop does. A worker that spent a
            # minute queued for a slot has not been stalled for a minute, and
            # the Watchdog must not treat it as if it had.
            self.last_step_ts = now()
            self.last_activity_ts = now()
            self._task = asyncio.create_task(self._run(), name=f"loop:{self.session.id}")
        return self._task

    def request_stop(self, status: str = "done") -> None:
        self._stop.set()
        self.store.touch_session(self.session, status=status, current_activity="")
        # Wake the loop so it notices the stop flag.
        with noted("waking the loop to stop", logging.DEBUG):
            self.inbox.put_nowait(Injection(text="", kind="system"))

    async def join(self, timeout: float = 10.0) -> None:
        if self._task:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._task), timeout)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def track_background(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    # ------------------------------------------------------------------ #
    # injection
    # ------------------------------------------------------------------ #
    def inject(self, text: str, *, kind: str = "event", trigger_id: str | None = None, **meta: Any) -> None:
        """Hand something to this session. Never blocks."""
        # The one door into a model's context from outside: no key goes through it.
        injection = Injection(text=scrubbed(text), kind=kind, trigger_id=trigger_id, meta=meta)
        self.inbox.put_nowait(injection)
        if injection.text and self.persist:
            self._queued.append({"uid": injection.uid, "text": injection.text, "kind": kind,
                                 "trigger_id": trigger_id, "meta": meta})
            self._keep_inbox()
        self.bus.emit(
            "session.injected",
            session_id=self.session.id, kind=kind,
            trigger_id=trigger_id, preview=text[:160], queued=self.inbox.qsize(),
        )
        call = self._call
        if (self.cut_in is not None and call is not None and not call.done()
                and not self._turn_from_person and self.cut_in(injection)):
            call.cancel()

    def note(self, text: str) -> None:
        """Something this session should know, without spending a turn on it: it
        goes in with whatever wakes the session next."""
        self._notes = [*self._notes[-19:], _clip(scrubbed(text), 3000)]
        self._save_notes()

    def _save_notes(self) -> None:
        if self.persist:
            self.store.save_notes(self.session.id, self._notes)

    def note_activity(self, what: str) -> None:
        self.last_activity_ts = now()
        self.store.touch_session(self.session, current_activity=what)

    async def _drain(self, *, block: bool) -> list[Injection]:
        """Collect everything queued. Blocks for the first item when asked to.

        This is the batching primitive: whatever piled up during the previous
        inference turn comes out together and is merged into one turn.
        """
        batch: list[Injection] = []
        if block:
            first = await self.inbox.get()
            if first.text:
                batch.append(first)
        cap = self.config.router_max_turn_batch
        while len(batch) < cap:
            try:
                item = self.inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item.text:
                batch.append(item)
        # Dropped from the saved inbox once the conversation holding them is saved.
        self._taken.update(item.uid for item in batch)
        return batch

    @staticmethod
    def _render_batch(batch: list[Injection]) -> str:
        if len(batch) == 1:
            return batch[0].text
        header = (
            f"{len(batch)} events arrived while you were working. "
            f"Handle all of them in this turn.\n"
        )
        parts = [f"--- event {i + 1}/{len(batch)} ({inj.kind}) ---\n{inj.text}"
                 for i, inj in enumerate(batch)]
        return header + "\n\n".join(parts)

    def _client_retrying(self, why: str) -> None:
        if self.on_struggling is not None:
            try:
                self.on_struggling(why)
            except Exception:  # noqa: BLE001 - only a courtesy
                log.debug("on_struggling failed", exc_info=True)

    def _from_person(self, items: list[Injection]) -> bool:
        return self.cut_in is not None and any(self.cut_in(i) for i in items)

    async def _think(self, step: Step, injected: str, batch_size: int):
        """One model call, which a person's new message may cut short.

        On a cut-in the call is abandoned before any tool runs, the new message
        is folded into this turn, and the model thinks again with both. Returns
        (response, injected, batch_size); response is None when stopping.
        """
        while True:
            begun = time.monotonic()
            with contextlib.suppress(AttributeError):
                self.client.on_retry = self._client_retrying
            call = asyncio.ensure_future(self.client.complete(
                system=self.system_prompt,
                messages=self.messages,
                tools=self.registry.specs(),
                max_tokens=self.max_tokens,
                role=self.role,
            ))
            self._call = call
            try:
                await asyncio.wait({call})
            finally:
                self._call = None
                if not call.done():
                    call.cancel()
            if not call.cancelled():
                return call.result(), injected, batch_size
            if self._stop.is_set():
                return None, injected, batch_size

            extra = await self._drain(block=False)
            self._turn_from_person = True
            self.cut_ins += 1
            # What the abandoned call cost: the output written before it was cut
            # (a streamed call stops there; one that cannot stream may run on and
            # be billed in full - then only the time is known).
            progress = getattr(self.client, "last_progress", None) or {}
            streamed = bool(getattr(self.client, "stream", False))
            wasted = int(progress.get("chars", 0) or 0) if streamed else 0
            waste = self.cut_in_waste
            waste["calls"] += 1
            waste["chars"] += wasted
            waste["seconds"] += time.monotonic() - begun
            waste["streamed"] = streamed
            if extra:
                text = "This arrived while you were thinking:\n" + self._render_batch(extra)
                last = self.messages[-1]
                content = (f"{last.content}\n\n{text}" if isinstance(last.content, str)
                           else [*last.content, {"type": "text", "text": text}])
                self.messages[-1] = Msg(role=last.role, content=content)
                injected = f"{injected}\n\n{text}"
                batch_size += len(extra)
                step.injected, step.batch_size = _clip(injected, 12000), batch_size
            # The fresh-reply check must count the folded-in message as seen.
            self.turn_started_ts = now()
            self.bus.emit("session.cut_in", session_id=self.session.id, merged=len(extra),
                          wasted_tokens=wasted // 4, streamed=streamed)

    # ------------------------------------------------------------------ #
    # the loop
    # ------------------------------------------------------------------ #
    async def _run(self) -> None:
        """Take in, think, act, settle - until stopped."""
        # A restored conversation may resume with tool results to send, or with
        # its last input to think about again (restore()).
        pending: list[dict[str, Any]] | None = self._restored_pending
        self._restored_pending = None
        injected, batch_size = self._resume_input, 0
        # Continue the numbering after a restart instead of starting at 0 again.
        step_index = self.session.step_count
        rounds = 0

        while not self._stop.is_set():
            try:
                taken = await self._take_in(pending, injected, batch_size)
                pending = None
                if taken is None:
                    if self._stop.is_set():
                        break
                    continue
                injected, batch_size, new_turn = taken
                if new_turn:
                    rounds = 0

                if self.max_steps and step_index >= self.max_steps:
                    self.bus.emit(
                        "session.capped", session_id=self.session.id, steps=step_index,
                        detail=f"hit the {self.max_steps}-step cap",
                    )
                    self.store.touch_session(self.session, status="failed",
                                             current_activity="step cap reached")
                    break

                # --- Spill the middle of the window if we are over budget ---
                self.messages, dropped = trim_messages(self.messages, self.budget_chars)
                if dropped:
                    self.spilled_turns += dropped
                    self.bus.emit("session.spilled", session_id=self.session.id, turns=dropped)

                self.store.touch_session(self.session, status="thinking")
                # Anything that arrives after this moment is news to the model -
                # the Router's reply tool uses it to avoid answering stale context.
                self.turn_started_ts = now()
                started = time.perf_counter()
                step = Step(
                    id=new_id("step"), session_id=self.session.id, index=step_index,
                    injected=_clip(injected, 12000), batch_size=batch_size,
                )

                try:
                    response, injected, batch_size = await self._think(step, injected, batch_size)
                    if response is None:          # stopped while a call was cut short
                        break
                    self._recovered()
                except ProviderAccountError as exc:
                    step_index += await self._blocked_by(exc, step, started)
                    continue
                except Exception as exc:
                    if not await self._failed(exc, step, started):
                        break
                    step_index += 1
                    continue

                self._record(step, response, started)
                pending, rounds, rested = await self._act(step, response, rounds)
                self.store.add_step(step)
                step_index += 1
                if pending is not None:
                    self._save(pending)              # results the model has not seen yet
                elif rested:
                    self._save(self._deferred, deferred=True)
                await self._end_of_turn(response)

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let one bad turn kill the session
                self.bus.emit(
                    "session.crash", session_id=self.session.id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self.store.touch_session(self.session, status="failed",
                                         current_activity=f"crashed: {exc}")
                break

        for task in list(self._background):
            task.cancel()
        if self.session.status in {"thinking", "tool", "idle", "waiting"}:
            self.store.touch_session(self.session, status="stopped", current_activity="")
        if self.session.status in {"done", "failed"}:
            # A finished clone's leftover events go nowhere: nothing waits for it.
            self._queued = []
            self._keep_inbox()
        self.bus.emit("session.finished", session_id=self.session.id, status=self.session.status)

    # ------------------------------------------------------------------ #
    # the steps of a turn
    # ------------------------------------------------------------------ #
    async def _take_in(self, pending: list[dict[str, Any]] | None, injected: str,
                       batch_size: int) -> tuple[str, int, bool] | None:
        """What the next model call answers: (text, events, whether a turn begins),
        or None when there is nothing to answer."""
        if self._retry_turn:
            # The last call failed, or the provider refused the account. Try the
            # same turn again, with anything new folded in.
            self._retry_turn = False
            extra = await self._drain(block=False)
            self.turn_events.extend(extra)
            if extra:
                text = "This arrived meanwhile:\n" + self._render_batch(extra)
                last = self.messages[-1]
                content = (f"{last.content}\n\n{text}" if isinstance(last.content, str)
                           else [*last.content, {"type": "text", "text": text}])
                self.messages[-1] = Msg(role=last.role, content=content)
                injected = f"{injected}\n\n{text}"
                batch_size += len(extra)
                self._save(None)
            return injected, batch_size, False

        if pending is None:
            batch = await self._drain(block=True)
            if self._stop.is_set() or not batch:
                return None
            injected = self._render_batch(batch)
            if self.annotate is not None:
                try:
                    extra_context = self.annotate(batch)
                except Exception as exc:  # noqa: BLE001 - context is optional
                    extra_context = ""
                    self.bus.emit("session.error", session_id=self.session.id, consecutive=0,
                                  error=f"annotate: {type(exc).__name__}: {exc}")
                if extra_context:
                    injected = f"{injected}\n\n{extra_context}"
            if self._notes:
                injected = "Since your last turn:\n" + "\n\n".join(self._notes) + f"\n\n{injected}"
                self._notes = []
                self._save_notes()
            content: Any = injected
            if self._deferred:
                # The results of the last turn's final tools come first: every
                # tool call must be answered before anything new.
                content = [*self._deferred, {"type": "text", "text": injected}]
                self._deferred = None
            self.messages.append(Msg(role="user", content=content))
            self._turn_from_person = self._from_person(batch)
            self.turn_events = list(batch)
            self.turn_opened_ts = now()
            # In the saved conversation from now on: a restart answers it.
            self._save(None)
            return injected, len(batch), True

        # Tool results, plus anything that arrived while tools ran.
        extra = await self._drain(block=False)
        self.turn_events.extend(extra)
        content = list(pending)
        if extra:
            content.append({"type": "text", "text": self._render_batch(extra)})
        self.messages.append(Msg(role="user", content=content))
        # The record clips long tool output but keeps the new events whole: a
        # relayed message must not vanish from the transcript because a file
        # read before it was long.
        results = "\n".join(str(blk.get("content", blk.get("text", ""))) for blk in pending)
        injected = _clip(results, 3000) + ("\n" + self._render_batch(extra) if extra else "")
        self._turn_from_person = self._from_person(extra)
        return injected, len(extra), False

    def _recovered(self) -> None:
        self.error_count = 0
        if self.blocked is not None:
            self.bus.emit("provider.recovered", session_id=self.session.id,
                          role=self.role, provider=self.blocked.provider)
            self.blocked = None
            self._blocked_wait = float(getattr(self.config, "blocked_retry_seconds", 30.0))

    async def _blocked_by(self, exc: ProviderAccountError, step: Step, started: float) -> int:
        """Out of credit, the key refused, or the person's own request budget used
        up. Not this turn's fault and not fixed by retrying at once: say so once,
        then try the same turn again now and then, so the work carries on by
        itself. Returns how many steps were recorded."""
        recorded = 0
        if self.blocked is None:
            step.error = f"{exc.plain()} ({exc.detail[:200]})"
            step.latency_ms = (time.perf_counter() - started) * 1000
            self.store.add_step(step)
            recorded = 1
            self.bus.emit("provider.blocked", session_id=self.session.id, role=self.role,
                          reason=exc.reason, provider=exc.provider, detail=exc.plain())
        self.blocked = exc
        self.store.touch_session(self.session, status="waiting",
                                 current_activity=f"{exc.plain()} - trying again by itself")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=self._blocked_wait)
        self._blocked_wait = min(self._blocked_wait * 2, 120.0)
        self._retry_turn = True
        return recorded

    async def _failed(self, exc: Exception, step: Step, started: float) -> bool:
        """A model call failed after the client's own retries. A passing failure
        (too many requests, the service down or unreachable) is tried again with a
        growing wait - found in real use: a turn dropped on HTTP 429 left three of
        the person's messages unanswered. A request the service refuses outright
        is given up after three tries, and said so. Returns False when this
        session gives up altogether."""
        self.error_count += 1
        step.error = f"{type(exc).__name__}: {exc}"
        step.latency_ms = (time.perf_counter() - started) * 1000
        self.store.add_step(step)
        self.bus.emit("session.error", session_id=self.session.id,
                      error=step.error, consecutive=self.error_count)
        # A clone gives up after five failures in a row. Rimuru does not: it is
        # the one who talks to people, so it waits and goes on.
        if self.error_count >= 5 and self.role != "router":
            self.store.touch_session(self.session, status="failed",
                                     current_activity=f"AI service error: {step.error[:120]}")
            return False
        again = passing_failure(exc) or self.error_count < 3
        if self.on_call_failed is not None:
            try:
                await self.on_call_failed(exc, again)
            except Exception:  # noqa: BLE001 - telling must not stop the work
                log.warning("telling about a failed call failed", exc_info=True)
        if again:
            self.store.touch_session(self.session, status="waiting",
                                     current_activity=f"AI service error, trying again: {step.error[:100]}")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=min(120.0, self.retry_base * 2 ** (self.error_count - 1)))
            self._retry_turn = True
        else:
            self.bus.emit("session.turn_given_up", session_id=self.session.id, error=step.error)
            self.error_count = 0
            self.store.touch_session(self.session, status="idle", current_activity="")
        return True

    def _record(self, step: Step, response: Any, started: float) -> None:
        """The model's answer: into the step, and into the conversation."""
        step.latency_ms = (time.perf_counter() - started) * 1000
        step.text = response.text
        step.thinking = response.thinking
        step.usage = response.usage or Usage()
        step.tool_calls = [
            {"id": c.id, "name": c.name, "args": c.args} for c in response.tool_calls
        ]

        # Record the assistant turn in provider-neutral blocks. Reasoning is
        # kept as its own block: clients that want it back (Kimi thinking
        # models) send it, the others strip it.
        assistant_blocks: list[dict[str, Any]] = []
        if response.text:
            assistant_blocks.append({"type": "text", "text": response.text})
        for call in response.tool_calls:
            assistant_blocks.append({
                "type": "tool_use", "id": call.id, "name": call.name, "input": call.args,
            })
        # Only alongside real content: a reasoning-only turn would become
        # an empty assistant message, which several APIs reject.
        if assistant_blocks and response.thinking:
            assistant_blocks.insert(0, {"type": "reasoning", "text": response.thinking})
        if assistant_blocks:
            self.messages.append(Msg(role="assistant", content=assistant_blocks))
        # Saved before the tools run: a restart while they run is resumed
        # with each one marked as cut off (restore()).
        self._save(None)

        self.last_step_ts = now()
        self.last_activity_ts = now()

        # A reasoning model can spend its whole output budget thinking
        # and stop before acting. Without this, the events in that turn
        # would simply be lost. Re-prompt (at most twice in a row).
        if response.stop_reason in {"length", "max_tokens"}:
            self.bus.emit(
                "session.truncated", session_id=self.session.id,
                acted=bool(response.tool_calls),
            )
        if response.stop_reason in {"length", "max_tokens"} and not response.tool_calls:
            self._truncations += 1
            if self._truncations <= 2:
                self.inject(
                    "Your previous turn hit the output-token limit before you "
                    "took any action, so nothing happened. Act now: keep the "
                    "reasoning short and call the tools.",
                    kind="system",
                )
        else:
            self._truncations = 0

    async def _act(self, step: Step, response: Any,
                   rounds: int) -> tuple[list[dict[str, Any]] | None, int, bool]:
        """Run the tools the model called. Returns (results it must see next,
        rounds of tools this turn, whether the turn rests here)."""
        if not response.tool_calls:
            self.store.touch_session(self.session, status="idle", current_activity="")
            return None, rounds, False
        self.store.touch_session(self.session, status="tool")
        results: list[dict[str, Any]] = []
        outputs: list[tuple[str, str]] = []
        for call in response.tool_calls:
            output = await self._invoke(call.name, call.args)
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": output})
            outputs.append((call.name, output))
            step.tool_results.append({"name": call.name, "output": output[:4000]})
            if self._stop.is_set():
                return None, rounds, False
        rounds += 1
        if self.inbox.empty() and self._may_rest(outputs, rounds):
            # Done: the model is not asked again just to read "sent". The results
            # go in first with whatever wakes it next.
            self._deferred = results
            self.store.touch_session(self.session, status="idle", current_activity="")
            return None, rounds, True
        return results, rounds, False

    def _may_rest(self, outputs: list[tuple[str, str]], rounds: int) -> bool:
        if self.max_rounds and rounds >= self.max_rounds:
            self.bus.emit("session.round_cap", session_id=self.session.id, rounds=rounds)
            return True
        if self.ends_turn is None:
            return False
        try:
            return bool(self.ends_turn(outputs))
        except Exception:  # noqa: BLE001 - when in doubt, let the model look
            log.warning("ends_turn failed", exc_info=True)
            return False

    async def _end_of_turn(self, response: Any) -> None:
        # The turn is over unless the model ran out of room mid-thought (it is
        # asked to act again in _record, so its text is not final).
        if (not response.tool_calls and self.on_turn_end is not None
                and response.stop_reason not in {"length", "max_tokens"}):
            try:
                await self.on_turn_end(response.text or "")
            except Exception as exc:  # noqa: BLE001 - a hook must not end the session
                self.bus.emit("session.error", session_id=self.session.id,
                              error=f"after the turn: {type(exc).__name__}: {exc}",
                              consecutive=0)

    async def _invoke(self, name: str, args: dict[str, Any]) -> str:
        tool = self.registry.get(name)
        if tool is None:
            return (
                f'No tool named "{name}". Available: {", ".join(self.registry.names())}'
            )
        self.bus.emit(
            "tool.called", session_id=self.session.id, tool=name,
            args=_safe(args), kind=self.session.kind,
        )
        self.note_activity(f"{name}(...)")
        try:
            result = tool.handler(args, self)
            if asyncio.iscoroutine(result):
                result = await result
            output = str(result)
        except Exception as exc:
            output = f"tool {name} failed: {type(exc).__name__}: {exc}"
        # Providers reject empty tool_result content on some paths. And whatever a
        # tool brought back - a command's output, say - no key goes to the AI service.
        output = scrubbed(output) or "(no output)"
        self.bus.emit(
            "tool.returned", session_id=self.session.id, tool=name, preview=output[:240],
        )
        return output


def passing_failure(exc: Exception) -> bool:
    """Too many requests, a server error, a dropped connection: worth trying the
    same turn again later. A refusal of this request (another HTTP 4xx) is not."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    text = str(exc)
    refused = re.search(r"HTTP (4\d\d)", text)
    return not refused or refused.group(1) in {"408", "409", "425", "429"}


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... {len(text) - limit} more characters not kept in this record]"


def _safe(args: dict[str, Any]) -> dict[str, Any]:
    try:
        json.dumps(args)
        return args
    except TypeError:
        return {k: str(v) for k, v in args.items()}
