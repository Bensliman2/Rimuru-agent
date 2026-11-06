"""Watchdog (article s6).

A persistently online system does not stay reliable for long on its internal
logic alone, so this is an independent Control Loop that lives outside the main
system and is driven by scheduled messages.

The part that matters is what it watches. A liveness probe is not enough here:
the common failure is not a crash but a session that is still perfectly alive
while looping unproductively or drifting off its goal. So the Watchdog checks
infrastructure AND task progress, the second with the model's judgement.

Because it is itself a full agent session, a maintainer can simply ask it why
something failed, and can have it correct the running system - disabling a
policy, nudging a worker - without a restart.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from ..agent.loop import AgentLoop
from ..agent.tools import ToolRegistry, enum, obj, register_recall, s
from ..ids import WATCHDOG_SESSION_ID
from ..models import Session

SYSTEM_PROMPT = """You are the Watchdog of a Super-agent called Rimuru.

You run outside the main system, on a timer. Each tick you are handed a health
report. Your job is to keep the system productive over long periods without a
human minding it.

Two kinds of trouble:

  INFRASTRUCTURE   API errors, sessions stuck in a failed state, work that never
                   started. Retry transient faults and keep rechecking until
                   they clear; do not give up after one look.

  PROGRESS         The harder and more common case. A session can be alive,
                   stepping, burning tokens and getting nowhere - repeating the
                   same tool call, waiting on a job that will never return, or
                   quietly working on something that is no longer the task it
                   was given. Judge whether each worker is doing substantive
                   work towards its actual goal.

Act in proportion. A nudge first. Stop a session only when it is clearly
unrecoverable or has drifted off its task entirely. Escalate to a human only for
things no amount of nudging will fix.

If the report shows nothing wrong, call assess(verdict=healthy) and stop. Doing
nothing is usually right, and a Watchdog that fiddles constantly is worse than
none.

When a maintainer asks you a question, investigate with recall() and answer it
plainly with answer().
"""


class WatchdogAgent:
    def __init__(
        self,
        *,
        store: Any,
        bus: Any,
        config: Any,
        client: Any,
        pool: Any,
        router: Any,
        speed: float = 1.0,
    ) -> None:
        self.store = store
        self.bus = bus
        self.config = config
        self.pool = pool
        self.router = router
        self.speed = max(speed, 0.01)
        self.ticks = 0
        self.interventions = 0

        self.session = store.sessions.get(WATCHDOG_SESSION_ID) or store.put_session(
            Session(
                id=WATCHDOG_SESSION_ID,
                kind="watchdog",
                title="Watchdog (external control loop)",
                model=config.model_for("watchdog"),
                status="idle",
            )
        )

        self.registry = ToolRegistry()
        self._register_tools()

        self.loop = AgentLoop(
            session=self.session,
            client=client,
            store=store,
            bus=bus,
            config=config,
            system_prompt=SYSTEM_PROMPT,
            registry=self.registry,
            role="watchdog",
            budget_chars=40_000,
            max_steps=0,
        )
        # Its checks are fresh each time: nothing to resume after a restart.
        self.loop.persist = False
        self._timer: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self.loop.start()
        self._timer = asyncio.create_task(self._tick_forever(), name="watchdog-timer")

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        self.loop.request_stop(status="stopped")

    def ask(self, question: str) -> None:
        """A maintainer asking in natural language."""
        self.loop.inject(
            f"A maintainer asks:\n{question}\n\n"
            f"Investigate with recall() if you need to, then answer with answer().",
            kind="human",
        )

    # ------------------------------------------------------------------ #
    # When the simulation compresses time, the Watchdog's sense of "a while"
    # has to compress with it, or it would never see anything happen.
    @property
    def interval(self) -> float:
        return max(4.0, self.config.watchdog_interval / self.speed)

    @property
    def stall_seconds(self) -> float:
        return max(10.0, self.config.worker_stall_seconds / self.speed)

    async def _tick_forever(self) -> None:
        # Let the system get going before the first look.
        await asyncio.sleep(min(self.interval, 15.0))
        while True:
            try:
                report = self.health_report()
                self.ticks += 1
                self.bus.emit("watchdog.tick", tick=self.ticks, findings=report["findings"])
                # Code finds the candidates; the model is only consulted when
                # there is something to judge, plus a sanity sweep every 4th tick.
                # An idle system costs nothing: the periodic sweep only runs
                # while clones are actually working.
                if report["findings"] or (self.ticks % 4 == 0 and self.pool.live):
                    self.loop.inject(report["text"], kind="system")
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.bus.emit("watchdog.error", error=f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(self.interval)

    # ------------------------------------------------------------------ #
    # Deterministic health collection: code finds candidates, the model judges.
    # ------------------------------------------------------------------ #
    def health_report(self) -> dict[str, Any]:
        now_ts = time.time()
        findings: list[str] = []
        lines: list[str] = ["=== health report ==="]

        router_session = self.store.sessions.get("rtr_main")
        if router_session:
            queued = self.router.loop.inbox.qsize() if self.router else 0
            lines.append(
                f"[rtr_main] router status={router_session.status} "
                f"steps={router_session.step_count} queued_events={queued} "
                f"errors={self.router.loop.error_count if self.router else 0}"
            )
            if self.router and self.router.loop.error_count >= 2:
                findings.append("router_api_errors")
            if queued > 25:
                findings.append("router_backlog")

        workers = self.pool.live if self.pool else []
        lines.append(f"live workers: {len(workers)} / limit {self.config.max_workers}")

        for worker in workers:
            session = worker.session
            steps = self.store.transcript(session.id)
            since_step = now_ts - worker.loop.last_step_ts
            pending = len(session.pending_jobs)

            recent_tools = [
                call.get("name") for step in steps[-4:] for call in step.tool_calls
            ]
            # A loop is the same call with the same arguments, over and over.
            # Reading four different files in a row is work, not a loop.
            recent_calls = [
                (call.get("name"), json.dumps(call.get("args") or {}, sort_keys=True))
                for step in steps[-4:] for call in step.tool_calls
            ]
            looping = len(recent_calls) >= 4 and len(set(recent_calls)) == 1

            # Waiting on the model is not a stall: a reasoning model writing a
            # whole file can legitimately think for minutes. Stalled means idle,
            # with no job running and nothing in flight - or a single call that
            # has hung far past any reasonable time.
            in_flight = session.status in {"thinking", "tool"}
            stalled = pending == 0 and (
                (since_step > self.stall_seconds and not in_flight)
                or since_step > self.stall_seconds * 4
            )
            flags: list[str] = []
            if stalled:
                flags.append("STALLED")
                findings.append(f"stalled:{session.id}")
            if looping:
                flags.append("LOOPING")
                findings.append(f"looping:{session.id}")
            if worker.loop.error_count >= 2:
                flags.append("API_ERRORS")
                findings.append(f"errors:{session.id}")

            lines.append(
                f"[{session.id}] task={session.task_id} status={session.status} "
                f"steps={session.step_count} last_step={since_step:.0f}s_ago "
                f"jobs_running={pending} {' '.join(flags)}"
            )
            lines.append(f'    goal: "{worker.task.title}"')
            lines.append(f"    doing: {session.current_activity or 'nothing recorded'}")
            if session.status == "thinking":
                # Found against a real model: without this line the Watchdog
                # read a 248-second generation (a worker writing a whole parser
                # in one reply) as "no progress for 3 minutes" and nudged it.
                lines.append(
                    f"    in flight: one model call has been running for {since_step:.0f}s. "
                    f"A single long generation (writing whole files) is normal and is "
                    f"not a stall by itself."
                )
            if recent_tools:
                lines.append(f"    recent tools: {', '.join(recent_tools[-6:])}")

        failed = [
            s for s in self.store.sessions.values()
            if s.status == "failed"
        ]
        if failed:
            findings.append("failed_sessions")
            lines.append("failed sessions: " + ", ".join(s.id for s in failed))

        stuck_tasks = [
            t for t in self.store.tasks.values()
            if t.status == "running" and t.duration and t.duration > 900
        ]
        if stuck_tasks:
            findings.append("long_running_tasks")
            for task in stuck_tasks:
                lines.append(
                    f'[{task.id}] "{task.title}" has been running '
                    f"{task.duration:.0f}s on {task.worker_id}"
                )

        if not findings:
            lines.append("No anomalies found by the programmatic checks.")

        return {"text": "\n".join(lines), "findings": findings}

    # ------------------------------------------------------------------ #
    # tools
    # ------------------------------------------------------------------ #
    def _register_tools(self) -> None:
        reg = self.registry
        register_recall(reg)

        reg.add(
            "assess",
            "Record your judgement for this tick. Use verdict=healthy and stop when "
            "nothing needs doing.",
            obj(
                verdict=enum("Overall state", ["healthy", "degraded", "unhealthy"]),
                note=s("One line of reasoning"),
            ),
            self._tool_assess,
        )

        reg.add(
            "nudge",
            "Send a corrective message into a running session. The first and "
            "lightest intervention: make it state its next concrete step, or point "
            "out that it has drifted.",
            obj(session_id=s("Session to nudge (wrk_... or rtr_main)"),
                text=s("What to tell it")),
            self._tool_nudge,
        )

        reg.add(
            "stop_session",
            "Stop a session that cannot recover. Its task is marked cancelled. Use "
            "this sparingly.",
            obj(session_id=s("Session (wrk_...)"), reason=s("Why it is unrecoverable")),
            self._tool_stop_session,
        )

        reg.add(
            "escalate",
            "Raise something to a human through the Router, for faults no "
            "intervention of yours can fix.",
            obj(text=s("What is wrong and what you have already tried")),
            self._tool_escalate,
        )

        reg.add(
            "answer",
            "Answer a maintainer's question. The answer is shown in the dashboard.",
            obj(text=s("The answer, plainly stated")),
            self._tool_answer,
        )

        reg.add(
            "health",
            "Re-collect the health report right now, rather than waiting for the "
            "next tick.",
            obj(),
            self._tool_health,
        )

    async def _tool_assess(self, args: dict[str, Any], loop: AgentLoop) -> str:
        verdict = str(args.get("verdict") or "healthy")
        note = str(args.get("note") or "")
        self.bus.emit("watchdog.assessed", verdict=verdict, note=note, tick=self.ticks)
        return f"Recorded: {verdict} - {note}"

    async def _tool_nudge(self, args: dict[str, Any], loop: AgentLoop) -> str:
        session_id = str(args.get("session_id") or "").strip()
        text = str(args.get("text") or "").strip()
        message = f"[watchdog] {text}"

        if session_id == "rtr_main" and self.router:
            self.router.loop.inject(message, kind="nudge")
            delivered = True
        else:
            delivered = self.pool.inject(session_id, message, kind="nudge")

        if delivered:
            self.interventions += 1
            self.bus.emit("watchdog.nudged", session_id=session_id, text=text)
            return f"Nudged {session_id}."
        return f"{session_id} is not a live session - nothing to nudge."

    async def _tool_stop_session(self, args: dict[str, Any], loop: AgentLoop) -> str:
        session_id = str(args.get("session_id") or "").strip()
        reason = str(args.get("reason") or "unrecoverable").strip()
        worker = self.pool.get(session_id) if self.pool else None
        if worker is None:
            return f"No worker {session_id}."
        await self.pool.stop(worker.task, reason=f"watchdog: {reason}")
        self.interventions += 1
        self.bus.emit("watchdog.stopped_session", session_id=session_id, reason=reason)
        return f"Stopped {session_id}: {reason}"

    async def _tool_escalate(self, args: dict[str, Any], loop: AgentLoop) -> str:
        text = str(args.get("text") or "").strip()
        if self.router:
            self.router.loop.inject(
                f"[watchdog escalation] {text}\n\n"
                f"This needs a human. Decide who to tell and in which channel.",
                kind="system",
            )
        self.interventions += 1
        self.bus.emit("watchdog.escalated", text=text)
        return "Escalated through the Router."

    async def _tool_answer(self, args: dict[str, Any], loop: AgentLoop) -> str:
        text = str(args.get("text") or "").strip()
        self.bus.emit("watchdog.answer", text=text)
        return "Answer delivered to the dashboard."

    async def _tool_health(self, args: dict[str, Any], loop: AgentLoop) -> str:
        return self.health_report()["text"]
