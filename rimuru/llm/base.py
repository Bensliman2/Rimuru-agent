"""Provider-agnostic LLM interface.

The engine only ever sees this surface, so the Router can run on a small cheap
model while Workers run on a strong one (article s2.1/s2.2), and either can be
swapped for DeepSeek, a local model, or the offline mock without touching the
architecture.
"""
from __future__ import annotations

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
