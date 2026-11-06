"""OpenAI-compatible backend: Kimi, DeepSeek, Ollama, vLLM, LM Studio, et al.

Translates the Anthropic-shaped content blocks the engine carries internally
into chat-completions messages and back.

Built for a real, rate-limited account rather than a demo:
  - one concurrency gate shared by every session, because limits are per account
  - retries with backoff on 429 / 5xx / dropped connections, honouring Retry-After
  - reasoning models: reasoning_content is captured for the transcript, and sent
    back on later turns only when the provider wants it (echo_reasoning)
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx

from ..models import Usage
from .base import LLMClient, LLMResponse, Msg, ProviderAccountError, ToolCall, ToolSpec, account_problem

_GATES: dict[tuple[int, int], asyncio.Semaphore] = {}
log = logging.getLogger("rimuru.llm")


def _gate(limit: int, server: str = "") -> asyncio.Semaphore:
    """One semaphore per event loop and server, shared by every client on it:
    rate limits are per account, and each job may use a different provider."""
    key = (id(asyncio.get_running_loop()), server, limit)
    gate = _GATES.get(key)
    if gate is None:
        gate = _GATES[key] = asyncio.Semaphore(max(1, limit))
    return gate


class ProviderError(RuntimeError):
    pass


class OpenAICompatClient(LLMClient):
    name = "openai"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 600.0,
        concurrency: int = 4,
        echo_reasoning: bool = False,
        max_attempts: int = 6,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.concurrency = concurrency
        self.echo_reasoning = echo_reasoning
        self.max_attempts = max_attempts
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        # From the model's entry in config/models.toml, when set there.
        self.max_tokens_override: int | None = None
        self.provider_label = ""
        # Rimuru's own calls stream (llm.build_client): when a person cuts in,
        # cancelling closes the stream and the provider stops writing, instead of
        # finishing - and billing - an answer nobody will read. last_progress
        # says how much had been written when it was cut (AgentLoop._think).
        self.stream = False
        self._stream_options = True
        self.last_progress: dict[str, Any] = {"chars": 0, "started": 0.0}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # A non-streaming reply sends nothing until generation finishes, and
            # a reasoning model writing a whole module can take minutes. A short
            # read timeout would abort that and retry from scratch - in a loop.
            # Connecting and sending stay on a short leash; reading does not.
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(
                connect=20.0, write=60.0, pool=60.0, read=self._timeout,
            ))
        return self._client

    @staticmethod
    def _tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in tools
        ]

    def _messages(self, system: str, messages: list[Msg]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if isinstance(m.content, str):
                out.append({"role": m.role, "content": m.content})
                continue

            if m.role == "assistant":
                text_parts: list[str] = []
                reasoning: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in m.content or []:
                    kind = block.get("type")
                    if kind == "text":
                        text_parts.append(block.get("text", ""))
                    elif kind == "reasoning":
                        reasoning.append(block.get("text", ""))
                    elif kind == "tool_use":
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input") or {}),
                            },
                        })
                msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                if reasoning and self.echo_reasoning:
                    msg["reasoning_content"] = "\n".join(reasoning)
                out.append(msg)
            else:
                # user turn: tool results and/or text
                leftovers: list[str] = []
                for block in m.content or []:
                    if block.get("type") == "tool_result":
                        out.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": str(block.get("content", "")) or "(no output)",
                        })
                    elif block.get("type") == "text":
                        leftovers.append(block.get("text", ""))
                if leftovers:
                    out.append({"role": "user", "content": "\n".join(leftovers)})
        return out

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error = "no attempt made"
        for attempt in range(self.max_attempts):
            backoff = min(20.0, 1.5 * (2 ** attempt)) + random.uniform(0, 0.5)
            try:
                async with _gate(self.concurrency, self.base_url):
                    response = await self._http().post(
                        f"{self.base_url}/chat/completions", json=payload, headers=headers
                    )
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                # Never retry silently: each retry regenerates (and bills) the
                # whole reply, so a timeout loop must show up in the logs.
                log.warning("%s: %s, retrying (attempt %d/%d)", self.model,
                            type(exc).__name__, attempt + 2, self.max_attempts)
                await asyncio.sleep(backoff)
                continue

            if response.status_code >= 400:
                # Out of credit, or the key refused: no retry helps, and the person
                # has to be told (found in real use: a 429 that meant "recharge").
                problem = account_problem(response.status_code, response.text)
                if problem:
                    raise ProviderAccountError(problem, self.provider_label or
                                               httpx.URL(self.base_url).host, response.text[:300])
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                log.warning("%s: HTTP %d, retrying (attempt %d/%d)", self.model,
                            response.status_code, attempt + 2, self.max_attempts)
                try:
                    retry_after = float(response.headers.get("retry-after", "") or 0)
                except ValueError:
                    retry_after = 0.0
                await asyncio.sleep(min(60.0, retry_after or backoff))
                continue

            if response.status_code >= 400:
                # A 4xx other than 429 will not get better by retrying.
                raise ProviderError(
                    f"HTTP {response.status_code} from {self.base_url} for model "
                    f"{self.model}: {response.text[:500]}"
                )
            return response.json()

        raise ProviderError(f"gave up after {self.max_attempts} attempts: {last_error}")

    async def _post_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The same request, streamed, read back into the shape _post returns.

        Errors before the first byte are handled exactly as in _post. A provider
        that cannot stream (or not with usage counts) is asked again without."""
        body: dict[str, Any] = {**payload, "stream": True}
        if self._stream_options:
            body["stream_options"] = {"include_usage": True}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        progress = self.last_progress = {"chars": 0, "started": time.monotonic()}

        last_error = "no attempt made"
        for attempt in range(self.max_attempts):
            backoff = min(20.0, 1.5 * (2 ** attempt)) + random.uniform(0, 0.5)
            status, text, retry_after = 0, "", ""
            try:
                async with _gate(self.concurrency, self.base_url):
                    async with self._http().stream("POST", f"{self.base_url}/chat/completions",
                                                   json=body, headers=headers) as response:
                        if response.status_code < 400:
                            return await self._read_stream(response, progress)
                        status = response.status_code
                        text = (await response.aread()).decode("utf-8", errors="replace")
                        retry_after = response.headers.get("retry-after", "")
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("%s: %s while streaming, retrying (attempt %d/%d)", self.model,
                            type(exc).__name__, attempt + 2, self.max_attempts)
                await asyncio.sleep(backoff)
                continue

            problem = account_problem(status, text)
            if problem:
                raise ProviderAccountError(problem, self.provider_label or
                                           httpx.URL(self.base_url).host, text[:300])
            if status == 429 or status >= 500:
                last_error = f"HTTP {status}: {text[:200]}"
                log.warning("%s: HTTP %d, retrying (attempt %d/%d)", self.model, status,
                            attempt + 2, self.max_attempts)
                try:
                    wait = float(retry_after or 0)
                except ValueError:
                    wait = 0.0
                await asyncio.sleep(min(60.0, wait or backoff))
                continue
            if "stream" in text.lower():
                if "stream_options" in body:          # no usage counts while streaming here
                    self._stream_options = False
                    body.pop("stream_options")
                    continue
                self.stream = False                   # this provider does not stream at all
                return await self._post(payload)
            raise ProviderError(f"HTTP {status} from {self.base_url} for model "
                                f"{self.model}: {text[:500]}")
        raise ProviderError(f"gave up after {self.max_attempts} attempts: {last_error}")

    @staticmethod
    async def _read_stream(response: httpx.Response, progress: dict[str, Any]) -> dict[str, Any]:
        """Server-sent chunks -> one chat-completions response: text, reasoning,
        tool calls arriving in pieces (by index), the finish reason and usage."""
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish: str | None = None
        usage: dict[str, Any] = {}
        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise ProviderError(f"the stream broke off: {str(chunk['error'])[:300]}")
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if piece := delta.get("content"):
                    content.append(piece)
                    progress["chars"] += len(piece)
                thought = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(thought, str) and thought:
                    reasoning.append(thought)
                    progress["chars"] += len(thought)
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(int(tc.get("index", len(calls)) or 0), {
                        "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    fn = tc.get("function") or {}
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    if fn.get("name") and not slot["function"]["name"]:
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
                        progress["chars"] += len(fn["arguments"])
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        message = {"content": "".join(content), "reasoning_content": "".join(reasoning),
                   "tool_calls": [calls[i] for i in sorted(calls)]}
        return {"choices": [{"message": message, "finish_reason": finish}], "usage": usage}

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
            "messages": self._messages(system, messages),
        }
        if tools:
            payload["tools"] = self._tools(tools)
            payload["tool_choice"] = "auto"

        data = await (self._post_stream(payload) if self.stream else self._post(payload))

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments", "")}
            if not isinstance(args, dict):
                args = {"_value": args}
            calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), args=args))

        u = data.get("usage") or {}
        cached = (
            (u.get("prompt_tokens_details") or {}).get("cached_tokens")
            or u.get("cached_tokens")          # Moonshot reports it at the top level
            or 0
        )
        usage = Usage(
            input_tokens=u.get("prompt_tokens", 0) or 0,
            output_tokens=u.get("completion_tokens", 0) or 0,
            cache_read_tokens=cached or 0,
        )

        return LLMResponse(
            text=(message.get("content") or "").strip(),
            thinking=(message.get("reasoning_content") or "").strip(),
            tool_calls=calls,
            usage=usage,
            stop_reason=choice.get("finish_reason") or "end_turn",
            raw=data,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
