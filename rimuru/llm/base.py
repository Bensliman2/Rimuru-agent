"""Provider-agnostic LLM interface.

The engine only ever sees this surface, so the Router can run on a small cheap
model while Workers run on a strong one (article s2.1/s2.2), and either can be
swapped for DeepSeek, a local model, or the offline mock without touching the
architecture.
"""
from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import Usage


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]           # JSON Schema for the arguments object


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Msg:
    role: str                        # user | assistant
    content: Any                     # str, or provider-shaped content blocks

    def as_text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        parts: list[str] = []
        for block in self.content or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", "")))
        return "\n".join(parts)


@dataclass
class LLMResponse:
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"
    raw: Any = None


class LLMClient(Protocol):
    name: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec],
        max_tokens: int = 2048,
        role: str = "worker",
    ) -> LLMResponse:
        ...


class RequestMeter:
    """How many requests went to each service in the last hour - free plans have
    a limit, and the person should hear before they hit it."""

    def __init__(self) -> None:
        self._calls: dict[str, deque[float]] = {}

    def note(self, service: str) -> None:
        self._calls.setdefault(service.rstrip("/"), deque()).append(time.time())

    def total(self, within: float = 3600.0) -> int:
        return sum(self.used(service, within) for service in list(self._calls))

    def used(self, service: str, within: float = 3600.0) -> int:
        calls = self._calls.get(service.rstrip("/"))
        if not calls:
            return 0
        while calls and calls[0] < time.time() - within:
            calls.popleft()
        return len(calls)


METER = RequestMeter()


class Budget:
    """The person's own cap on AI requests an hour, across every service
    (RIMURU_MAX_REQUESTS_PER_HOUR); 0 means none."""

    def __init__(self) -> None:
        self.per_hour = 0


BUDGET = Budget()


def check_budget(provider: str) -> None:
    """Before each request: refuse once the hour's budget is used. The loop then
    waits and tries again by itself, and the person is told (provider.blocked)."""
    if BUDGET.per_hour and METER.total() >= BUDGET.per_hour:
        raise ProviderAccountError("budget", provider or "The AI service",
                                   f"{BUDGET.per_hour} requests an hour")


# Who goes first when the requests to one service queue up: the person's chat,
# then the clones doing their work, then the agents in the background.
PRIORITY = {"router": 0, "worker": 1, "watchdog": 2, "filter": 2}


class PriorityGate:
    """A semaphore that lets the most urgent waiter in first."""

    def __init__(self, limit: int) -> None:
        self._free = max(1, limit)
        self._queue: list[tuple[int, int, asyncio.Future]] = []
        self._seq = itertools.count()

    async def acquire(self, priority: int) -> None:
        if self._free > 0 and not any(not f.done() for _p, _n, f in self._queue):
            self._free -= 1
            return
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._queue, (priority, next(self._seq), future))
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release()                  # a slot was handed over just as it was cancelled
            raise

    def release(self) -> None:
        while self._queue:
            _priority, _n, future = heapq.heappop(self._queue)
            if not future.done():
                future.set_result(None)
                return
        self._free += 1

    @contextlib.asynccontextmanager
    async def slot(self, priority: int):
        await self.acquire(priority)
        try:
            yield
        finally:
            self.release()


class ProviderAccountError(RuntimeError):
    """The provider refuses because of the account, not the request: it is out of
    credit, or the key is not accepted. Retrying at once cannot help - the person
    has to act - so the app must tell them, and keep trying quietly meanwhile."""

    def __init__(self, reason: str, provider: str, detail: str = "") -> None:
        self.reason = reason                       # "credit" | "key"
        self.provider = provider or "The AI provider"
        self.detail = detail
        super().__init__(f"{self.provider}: {reason} - {detail[:300]}")

    def plain(self) -> str:
        if self.reason == "credit":
            return f"{self.provider} says the account is out of credit"
        if self.reason == "budget":
            return f"the limit you set ({self.detail}) is used up for this hour"
        return f"{self.provider} did not accept the API key"


_CREDIT = ("insufficient balance", "insufficient_quota", "exceeded_current_quota",
           "exceeded your current quota", "credit balance", "recharge", "payment required",
           "out of credit", "billing details")
_KEY = ("invalid api key", "invalid_api_key", "incorrect api key", "invalid authentication",
        "authentication", "unauthorized", "api key")


def account_problem(status: int | None, text: str) -> str | None:
    """'credit' or 'key' when an API error is about the account, else None."""
    low = (text or "").lower()
    if status == 402 or any(k in low for k in _CREDIT):
        return "credit"
    if status == 401 or (status == 403 and any(k in low for k in _KEY)):
        return "key"
    return None
