"""A tiny async pub/sub bus.

Two consumers: the engine itself (routing decisions and job completions), and the
web UI, which mirrors every emission out over a WebSocket so you can watch the
system think in real time.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]

# What the pages tell as the story of what happened ("Latest", "What it decided
# and said", "What it did"). These are saved, so a restart does not blank them;
# the rest - steps, status updates - already live in their own records.
STORY = frozenset({
    "filter.decided", "trigger.routed", "tool.called", "worker.spawned", "worker.queued",
    "worker.finished", "worker.handback", "thread.subscribed", "channel.sent",
    "watchdog.nudged", "watchdog.assessed", "watchdog.escalated", "watchdog.answer",
    "session.cut_in", "router.reply_held", "safety.hidden_instructions", "session.error",
    "session.spilled", "speculation.discarded", "models.changed", "sim.started",
    "sim.completed", "system.started",
})


def compact(event: dict[str, Any]) -> dict[str, Any]:
    """An event as it is saved: long text cut - a tool call can carry a whole file."""
    out: dict[str, Any] = {}
    for key, value in event.items():
        if isinstance(value, str):
            out[key] = value[:1000]
        elif key == "args" and isinstance(value, dict):
            out[key] = {k: v[:300] if isinstance(v, str) else v for k, v in value.items()}
        else:
            out[key] = value
    return out

log = logging.getLogger("rimuru.bus")


class Bus:
    def __init__(self, history: int = 2000) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._queues: list[asyncio.Queue] = []
        self.history: deque[dict[str, Any]] = deque(maxlen=history)
        self._seq = 0

    def restore(self, events: list[dict[str, Any]]) -> None:
        """Saved events, back in the history after a restart."""
        self.history.extend(events)
        self._seq = max([self._seq, *(int(e.get("seq") or 0) for e in events)])

    def on(self, topic: str, handler: Handler) -> None:
        """Register a handler. Topic '*' receives everything."""
        self._handlers.setdefault(topic, []).append(handler)

    def subscribe_queue(self, maxsize: int = 600) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._queues.append(q)
        return q

    def unsubscribe_queue(self, q: asyncio.Queue) -> None:
        with contextlib.suppress(ValueError):
            self._queues.remove(q)

    def emit(self, topic: str, **payload: Any) -> dict[str, Any]:
        """Fire and forget. Safe to call from synchronous code."""
        self._seq += 1
        event = {"seq": self._seq, "topic": topic, "ts": time.time(), **payload}
        self.history.append(event)

        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A slow UI client must never stall the engine: drop the oldest.
                with contextlib.suppress(asyncio.QueueEmpty, asyncio.QueueFull):
                    q.get_nowait()
                    q.put_nowait(event)

        for handler in self._handlers.get(topic, []) + self._handlers.get("*", []):
            try:
                result = handler(event)
            except Exception:  # a broken listener must not take down the engine
                log.warning("a listener for %s failed", topic, exc_info=True)
                continue
            if asyncio.iscoroutine(result):
                try:
                    asyncio.get_running_loop().create_task(result)
                except RuntimeError:
                    result.close()
        return event

    def recent(self, limit: int = 200, topics: set[str] | None = None) -> list[dict[str, Any]]:
        items = list(self.history)
        if topics:
            items = [e for e in items if e["topic"] in topics]
        return items[-limit:]
