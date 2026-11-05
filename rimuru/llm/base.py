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
