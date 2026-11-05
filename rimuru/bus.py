"""A tiny async pub/sub bus.

Two consumers: the engine itself (routing decisions and job completions), and the
web UI, which mirrors every emission out over a WebSocket so you can watch the
system think in real time.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import Any, Awaitable, Callable

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]


class Bus:
    def __init__(self, history: int = 2000) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._queues: list[asyncio.Queue] = []
        self.history: deque[dict[str, Any]] = deque(maxlen=history)
        self._seq = 0

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
                with contextlib.suppress(Exception):
                    q.get_nowait()
                    q.put_nowait(event)

        for handler in self._handlers.get(topic, []) + self._handlers.get("*", []):
            try:
                result = handler(event)
            except Exception:  # a broken listener must not take down the engine
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
