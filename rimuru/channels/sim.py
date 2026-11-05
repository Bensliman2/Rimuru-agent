"""The local simulation channel.

Replays a scripted scenario into the system at wall-clock speed (optionally
compressed), and accepts the agent's replies back. The article scales message
intervals and work durations by 1/4 in its own experiment; `speed` here does the
same job and applies to simulated job durations too, so a 20-minute workload can
be watched in five.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from ..ids import new_id
from ..models import Trigger
from .base import ChannelNorms, ASYNC_NORMS
from .scenario import Scenario, ScriptedEvent

Ingest = Callable[[Trigger], Awaitable[Any]]


class SimChannel:
    """Acts as several sources at once: im, github and ci."""

    name = "sim"
    norms = ChannelNorms()

    def __init__(self, bus: Any) -> None:
        self.bus = bus
        self.delivered: list[dict[str, Any]] = []

    async def deliver(self, thread_key: str, text: str) -> str:
        entry = {"ts": time.time(), "thread_key": thread_key, "text": text}
        self.delivered.append(entry)
        self.bus.emit("sim.reply", **entry)
        return text


class GitHubSimChannel(SimChannel):
    """Same simulation, async-channel manners: dense and structured."""

    name = "github"
    norms = ASYNC_NORMS


class ImSimChannel(SimChannel):
    name = "im"
    norms = ChannelNorms()


class SimRunner:
    """Drives a Scenario into the system."""

    def __init__(
        self,
        *,
        bus: Any,
        ingest: Ingest,
        speed: float = 4.0,
    ) -> None:
        self.bus = bus
        self.ingest = ingest
        self.speed = max(speed, 0.01)
        self.scenario: Scenario | None = None
        self.task: asyncio.Task | None = None
        self.started_ts: float | None = None
        self.sent = 0
        self.paused = asyncio.Event()
        self.paused.set()          # set == running
        self._inflight: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> dict[str, Any]:
        elapsed = (time.time() - self.started_ts) if self.started_ts else 0.0
        return {
            "running": self.running,
            "paused": not self.paused.is_set(),
            "scenario": self.scenario.summary() if self.scenario else None,
            "sent": self.sent,
            "total": len(self.scenario.events) if self.scenario else 0,
            "elapsed": round(elapsed, 1),
            "speed": self.speed,
        }

    # ------------------------------------------------------------------ #
    def start(self, scenario: Scenario, *, speed: float | None = None) -> None:
        if self.running:
            raise RuntimeError("a scenario is already running")
        if speed is not None:
            self.speed = max(speed, 0.01)
        self.scenario = scenario
        self.sent = 0
        self.started_ts = time.time()
        self.paused.set()
        self.task = asyncio.create_task(self._run(), name="sim-runner")
        self.bus.emit(
            "sim.started", scenario=scenario.summary(), speed=self.speed,
        )

    def stop(self) -> None:
        if self.task:
            self.task.cancel()
        self.bus.emit("sim.stopped", sent=self.sent)

    def pause(self) -> None:
        self.paused.clear()
        self.bus.emit("sim.paused", sent=self.sent)

    def resume(self) -> None:
        self.paused.set()
        self.bus.emit("sim.resumed", sent=self.sent)

    # ------------------------------------------------------------------ #
    async def _run(self) -> None:
        assert self.scenario is not None
        begin = time.monotonic()
        try:
            for event in self.scenario.events:
                target = begin + (event.offset / self.speed)
                while True:
                    await self.paused.wait()
                    delay = target - time.monotonic()
                    if delay <= 0:
                        break
                    await asyncio.sleep(min(delay, 0.25))

                # Do NOT await the full pass. The environment does not wait for
                # the agent to finish judging one message before producing the
                # next, and awaiting here would throttle arrivals to the speed of
                # the semantic filter - which is exactly the queueing the Router's
                # batching is supposed to absorb.
                task = asyncio.create_task(self.ingest(self._to_trigger(event)))
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)
                self.sent += 1

            if self._inflight:
                await asyncio.gather(*self._inflight, return_exceptions=True)
            self.bus.emit("sim.completed", sent=self.sent)
        except asyncio.CancelledError:
            for task in list(self._inflight):
                task.cancel()
            self.bus.emit("sim.cancelled", sent=self.sent)
            raise

    @staticmethod
    def _to_trigger(event: ScriptedEvent) -> Trigger:
        return Trigger(
            id=new_id("trigger"),
            channel=event.channel,
            thread_key=event.thread_key,
            author=event.author,
            kind=event.kind,
            content=event.content,
            meta=dict(event.meta),
        )
