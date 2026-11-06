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
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..config import Config
from ..context import trim_messages
from ..ids import new_id
from ..llm.base import LLMClient, Msg, ProviderAccountError
from ..models import Session, Step, Usage, now
from .tools import ToolRegistry


@dataclass
class Injection:
    """One thing waiting to enter a session's context."""

    text: str
    kind: str = "event"              # event | job | human | system | nudge
    trigger_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


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

    # ------------------------------------------------------------------ #
    # saved conversations: resuming exactly where a restart cut in
    # ------------------------------------------------------------------ #
    def _save(self, pending: list[dict[str, Any]] | None) -> None:
        if not self.persist:
            return
        try:
            self.store.save_context(self.session.id, {
                "messages": [{"role": m.role, "content": m.content} for m in self.messages],
                "pending": pending,
            })
        except Exception as exc:  # noqa: BLE001 - losing a save must not stop the work
            self.bus.emit("session.error", session_id=self.session.id, consecutive=0,
                          error=f"could not save the conversation: {type(exc).__name__}: {exc}")

    def restore(self, saved: dict[str, Any]) -> None:
        """Take up a saved conversation exactly where it stopped.

        Three ways a restart can cut in: after tools ran but before the model saw
        their results (they are sent now); while tools were running (each gets a
        result saying it was cut off - the model checks before relying on it); or
        while the model was thinking (it is asked again)."""
        self.messages = [Msg(role=m["role"], content=m["content"]) for m in saved.get("messages") or []]
        last = self.messages[-1] if self.messages else None
        calls = ([b for b in last.content if isinstance(b, dict) and b.get("type") == "tool_use"]
                 if last is not None and last.role == "assistant" and isinstance(last.content, list)
                 else [])
        if saved.get("pending"):
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
        with contextlib.suppress(Exception):
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
        injection = Injection(text=text, kind=kind, trigger_id=trigger_id, meta=meta)
        self.inbox.put_nowait(injection)
        self.bus.emit(
            "session.injected",
            session_id=self.session.id, kind=kind,
            trigger_id=trigger_id, preview=text[:160], queued=self.inbox.qsize(),
        )
        call = self._call
        if (self.cut_in is not None and call is not None and not call.done()
                and not self._turn_from_person and self.cut_in(injection)):
            call.cancel()

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
        # A restored conversation may resume with tool results to send, or with
        # its last input to think about again (restore()).
        pending_results: list[dict[str, Any]] | None = self._restored_pending
        self._restored_pending = None
        injected, batch_size = self._resume_input, 0
        # Continue the numbering after a restart instead of starting at 0 again.
        step_index = len(self.store.transcript(self.session.id))

        while not self._stop.is_set():
            try:
                if self._retry_turn:
                    # The provider refused this turn (see ProviderAccountError
                    # below). Try the same turn again, with anything new folded in.
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
                elif pending_results is None:
                    batch = await self._drain(block=True)
                    if self._stop.is_set():
                        break
                    if not batch:
                        continue
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
                    self.messages.append(Msg(role="user", content=injected))
                    batch_size = len(batch)
                    self._turn_from_person = self._from_person(batch)
                    self.turn_events = list(batch)
                    self.turn_opened_ts = now()
                else:
                    # Tool results, plus anything that arrived while tools ran.
                    extra = await self._drain(block=False)
                    self.turn_events.extend(extra)
                    content: list[dict[str, Any]] = list(pending_results)
                    if extra:
                        content.append({"type": "text", "text": self._render_batch(extra)})
                    self.messages.append(Msg(role="user", content=content))
                    # The record clips long tool output but keeps the new events
                    # whole: a relayed message must not vanish from the transcript
                    # because a file read before it was long.
                    results = "\n".join(
                        str(blk.get("content", blk.get("text", ""))) for blk in pending_results
                    )
                    injected = _clip(results, 3000) + (
                        "\n" + self._render_batch(extra) if extra else "")
                    batch_size = len(extra)
                    pending_results = None
                    self._turn_from_person = self._from_person(extra)

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
                    self.error_count = 0
                    if self.blocked is not None:
                        self.bus.emit("provider.recovered", session_id=self.session.id,
                                      role=self.role, provider=self.blocked.provider)
                        self.blocked = None
                        self._blocked_wait = float(getattr(self.config, "blocked_retry_seconds", 30.0))
                except ProviderAccountError as exc:
                    # Out of credit, or the key refused. Not this turn's fault and
                    # not fixed by retrying at once: say so once, then try the same
                    # turn again now and then, so the work carries on by itself
                    # when the person has fixed it.
                    if self.blocked is None:
                        step.error = f"{exc.plain()} ({exc.detail[:200]})"
                        step.latency_ms = (time.perf_counter() - started) * 1000
                        self.store.add_step(step)
                        step_index += 1
                        self.bus.emit("provider.blocked", session_id=self.session.id, role=self.role,
                                      reason=exc.reason, provider=exc.provider, detail=exc.plain())
                    self.blocked = exc
                    self.store.touch_session(self.session, status="waiting",
                                             current_activity=f"{exc.plain()} - trying again by itself")
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=self._blocked_wait)
                    self._blocked_wait = min(self._blocked_wait * 2, 120.0)
                    self._retry_turn = True
                    continue
                except Exception as exc:
                    self.error_count += 1
                    step.error = f"{type(exc).__name__}: {exc}"
                    step.latency_ms = (time.perf_counter() - started) * 1000
                    self.store.add_step(step)
                    self.bus.emit(
                        "session.error", session_id=self.session.id,
                        error=step.error, consecutive=self.error_count,
                    )
                    self.store.touch_session(self.session, status="waiting",
                                             current_activity=f"API error: {step.error[:120]}")
                    # A clone gives up after five failures in a row. Rimuru does not:
                    # it is the one who talks to people, so it waits and goes on.
                    if self.error_count >= 5 and self.role != "router":
                        self.store.touch_session(self.session, status="failed")
                        break
                    await asyncio.sleep(min(10.0, 1.5 * self.error_count))
                    step_index += 1
                    continue

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

                # --- run the tools ---
                if response.tool_calls:
                    self.store.touch_session(self.session, status="tool")
                    results: list[dict[str, Any]] = []
                    for call in response.tool_calls:
                        output = await self._invoke(call.name, call.args)
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": output,
                        })
                        step.tool_results.append({"name": call.name, "output": output[:4000]})
                        if self._stop.is_set():
                            break
                    pending_results = results if not self._stop.is_set() else None
                else:
                    self.store.touch_session(self.session, status="idle", current_activity="")

                self.store.add_step(step)
                step_index += 1
                if pending_results is not None:
                    self._save(pending_results)     # results the model has not seen yet

                # The turn is over unless the model ran out of room mid-thought
                # (it is asked to act again above, so its text is not final).
                if (not response.tool_calls and self.on_turn_end is not None
                        and response.stop_reason not in {"length", "max_tokens"}):
                    try:
                        await self.on_turn_end(response.text or "")
                    except Exception as exc:  # noqa: BLE001 - a hook must not end the session
                        self.bus.emit("session.error", session_id=self.session.id,
                                      error=f"after the turn: {type(exc).__name__}: {exc}",
                                      consecutive=0)

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
        self.bus.emit("session.finished", session_id=self.session.id, status=self.session.status)

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
        # Providers reject empty tool_result content on some paths.
        output = output or "(no output)"
        self.bus.emit(
            "tool.returned", session_id=self.session.id, tool=name, preview=output[:240],
        )
        return output


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
