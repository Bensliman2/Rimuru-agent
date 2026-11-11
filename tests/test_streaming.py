"""Cutting in has a smaller cost: Rimuru's calls stream, so a person's message
that cuts in closes the stream and the provider stops writing - and what had
been written is measured. Offline: a fake provider speaking server-sent events."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx

from rimuru.config import Config
from rimuru.llm.base import LLMResponse, Msg
from rimuru.llm.mock import MockClient
from rimuru.llm.openai_client import OpenAICompatClient
from rimuru.system import RimuruSystem


def sse(*chunks: dict) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def delta(**d) -> dict:
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def client_with(handler) -> OpenAICompatClient:
    client = OpenAICompatClient("http://fake.test/v1", "k", "m", max_attempts=2)
    client.stream = True
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def ask(client: OpenAICompatClient) -> LLMResponse:
    return await client.complete(system="s", messages=[Msg("user", "hi")], tools=[])


def test_a_streamed_answer_reads_back_like_a_normal_one():
    body = sse(
        delta(reasoning_content="The person wants "), delta(reasoning_content="a page."),
        delta(content="On it"), delta(content=" now."),
        delta(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                           "function": {"name": "dispatch_task", "arguments": ""}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '{"title": "Bui'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'ld a page"}'}}]),
        delta(tool_calls=[{"index": 1, "id": "c2", "type": "function",
                           "function": {"name": "reply", "arguments": '{"text": "ok"}'}}]),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30,
                                  "prompt_tokens_details": {"cached_tokens": 100}}},
    )
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    response = asyncio.run(ask(client_with(handler)))
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    assert response.text == "On it now." and response.thinking == "The person wants a page."
    assert [(c.name, c.args) for c in response.tool_calls] == [
        ("dispatch_task", {"title": "Build a page"}), ("reply", {"text": "ok"})]
    assert (response.usage.input_tokens, response.usage.output_tokens,
            response.usage.cache_read_tokens) == (120, 30, 100)
    assert response.stop_reason == "tool_calls"


def test_cutting_in_stops_reading_the_answer_and_says_how_far_it_got():
    pulled = {"n": 0}

    async def slow():
        for i in range(400):
            pulled["n"] += 1
            yield f"data: {json.dumps(delta(content='word '))}\n\n".encode()
            await asyncio.sleep(0.01)
        yield b"data: [DONE]\n\n"

    async def run():
        client = client_with(lambda _req: httpx.Response(200, content=slow()))
        call = asyncio.create_task(ask(client))
        while client.last_progress["chars"] < 50:
            await asyncio.sleep(0.005)
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        at_cut = pulled["n"]
        await asyncio.sleep(0.2)
        return at_cut, pulled["n"], client.last_progress["chars"]

    at_cut, later, chars = asyncio.run(run())
    assert later == at_cut, "the answer kept being read after the cut-in"
    assert at_cut < 400 and chars >= 50


def test_a_provider_that_cannot_stream_is_asked_again_without():
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        asked.append(body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": "unknown field stream_options"})
        if body.get("stream"):
            return httpx.Response(400, json={"error": "streaming is not supported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"},
                                                      "finish_reason": "stop"}]})

    client = client_with(handler)
    response = asyncio.run(ask(client))
    assert response.text == "hello"
    assert ["stream_options" in a for a in asked] == [True, False, False]
    assert client.stream is False, "it remembers this provider cannot stream"


def test_only_rimurus_calls_stream(monkeypatch):
    from rimuru import llm
    from rimuru.llm import catalog

    monkeypatch.setattr(catalog, "make_client", lambda *_a, **_k: SimpleNamespace(stream=False))
    config = Config()
    config.demo = False
    config.setup = SimpleNamespace(ready=True, entry_for=lambda kind: object())
    assert llm.build_client(config, "router").stream is True
    assert llm.build_client(config, "worker").stream is False


def test_each_cut_in_records_what_was_thrown_away(tmp_path):
    async def run():
        config = Config()
        config.provider = "mock"
        config.db_path, config.workspace = tmp_path / "t.db", tmp_path / "ws"
        config.sessions_dir = tmp_path / "sessions"
        system = RimuruSystem(config, speed=50.0)
        calls = []

        class Writing(MockClient):
            stream = True

            def __init__(self):
                super().__init__()
                self.last_progress = {"chars": 0}

            async def complete(self, **kwargs):
                calls.append(time.time())
                self.last_progress = {"chars": 0}
                if len(calls) == 1:
                    for _ in range(300):                  # a long answer about a report
                        self.last_progress["chars"] += 20
                        await asyncio.sleep(0.01)
                return LLMResponse(text="ok")

        system.router.loop.client = Writing()
        await system.start()
        system.router.loop.inject("[clone report] the page is done", kind="event")
        while system.router.loop.client.last_progress["chars"] < 200:
            await asyncio.sleep(0.01)
        await system.human_message("How is it going?")
        deadline = time.time() + 5
        while len(calls) < 2 and time.time() < deadline:
            await asyncio.sleep(0.01)
        waste = dict(system.router.loop.cut_in_waste)
        overview = system.overview()["cut_in_waste"]
        events = [e for e in system.bus.history if e["topic"] == "session.cut_in"]
        await system.stop()
        return waste, overview, events

    waste, overview, events = asyncio.run(run())
    assert waste["calls"] == 1 and waste["chars"] >= 200 and waste["streamed"] is True
    assert overview["tokens"] == waste["chars"] // 4 and overview["calls"] == 1
    assert events and events[0]["wasted_tokens"] >= 50
