"""Claude backend.

Prompt caching is switched on for the system prompt, which matters here: the
Router is one long-lived session that is re-prefilled on every turn, so almost
all of its input should be cache reads. The article tracks exactly this number
(cache-read tokens per run) when comparing designs in s7.1.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..models import Usage
from .base import LLMClient, LLMResponse, Msg, ProviderAccountError, ToolCall, ToolSpec, account_problem


class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, base_url: str | None = None) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The anthropic package is required for the Claude backend: "
                "pip install -r requirements.txt"
            ) from exc
        self._client = AsyncAnthropic(api_key=api_key, **({"base_url": base_url} if base_url else {}))
        self.model = model
        # From the model's entry in config/models.toml, when set there.
        self.max_tokens_override: int | None = None
        self.provider_label = ""

    _BLOCKS = {"text", "tool_use", "tool_result", "image"}

    @classmethod
    def _content(cls, content: Any) -> Any:
        """Drop engine-internal block types (e.g. 'reasoning') the API rejects."""
        if isinstance(content, str):
            return content
        kept = [b for b in content or [] if isinstance(b, dict) and b.get("type") in cls._BLOCKS]
        return kept or [{"type": "text", "text": "(empty)"}]

    @staticmethod
    def _tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.schema}
            for t in tools
        ]

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec],
        max_tokens: int = 2048,
        role: str = "worker",
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens_override or max_tokens,
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": m.role, "content": self._content(m.content)} for m in messages],
        }
        if tools:
            payload["tools"] = self._tools(tools)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                raw = await self._client.messages.create(**payload)
                break
            except Exception as exc:  # transient API failure: the Watchdog also sees these
                problem = account_problem(getattr(exc, "status_code", None), str(exc))
                if problem:
                    raise ProviderAccountError(problem, self.provider_label or "Claude (Anthropic)",
                                               str(exc)[:300]) from exc
                last_error = exc
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
        else:  # pragma: no cover
            raise last_error or RuntimeError("anthropic call failed")

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in raw.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "thinking":
                thinking_parts.append(getattr(block, "thinking", ""))
            elif btype == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, args=dict(block.input or {})))

        u = raw.usage
        usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )

        return LLMResponse(
            text="\n".join(text_parts).strip(),
            thinking="\n".join(thinking_parts).strip(),
            tool_calls=calls,
            usage=usage,
            stop_reason=getattr(raw, "stop_reason", "end_turn") or "end_turn",
            raw=raw,
        )
