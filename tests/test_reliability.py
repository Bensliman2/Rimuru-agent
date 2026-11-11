"""Never lose a message, never run away (docs/production-plan.md, phase 0).

A fake AI service that fails in bursts, refuses, or keeps calling the same tool:
no message is lost, the person is told, and the number of calls stays bounded.
Offline.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

from rimuru.agent.loop import AgentLoop, passing_failure
from rimuru.agent.tools import ToolRegistry, obj, s
from rimuru.bus import Bus
from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.llm.base import (BUDGET, METER, LLMResponse, PriorityGate, ProviderAccountError, ToolCall,
                             check_budget)
from rimuru.llm.fallback import FallbackClient
from rimuru.llm.openai_client import ProviderError
from rimuru.models import Session, Task
from rimuru.store import Store
from rimuru.system import RimuruSystem

BUSY = ProviderError("gave up after 6 attempts: HTTP 429: too many requests")
REFUSED = ProviderError("HTTP 400 from https://x for model m: bad request")


class Script:
    """A fake AI service: answers in order; the last answer repeats."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[list] = []

    async def complete(self, *, system, messages, tools, max_tokens=2048, role="worker"):
        self.calls.append([m.content for m in messages])
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer(messages) if callable(answer) else answer


def calls(*names):
    return LLMResponse(text="", tool_calls=[ToolCall(id=new_id("step"), name=n, args={}) for n in names])


DONE = LLMResponse(text="ok", tool_calls=[])


def make_loop(tmp_path: Path, client, *, tools=("reply",), role="router", ends=None, store=None, session_id="rtr_t"):
    store = store or Store(tmp_path / "t.db")
    registry = ToolRegistry()
    for name in tools:
        registry.add(name, name, obj(text=s("text", optional=True)),
                     lambda args, loop, name=name: "Sent on console s-1: hi" if name == "reply" else f"{name} done")
    session = store.put_session(Session(id=session_id, kind="router", title="t", model="m"))
    loop = AgentLoop(session=session, client=client, store=store, bus=Bus(), config=Config(),
                     system_prompt="", registry=registry, role=role, budget_chars=50_000)
    loop.retry_base = 0.01
    loop.ends_turn = ends
    return loop, store


async def settle(loop, until, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end and not until():
        await asyncio.sleep(0.02)
    assert until(), "timed out"


def test_a_turn_that_did_its_job_is_not_asked_again(tmp_path):
    async def run():
        client = Script(calls("reply"), DONE)
        loop, store = make_loop(tmp_path, client, ends=lambda outs: all(o.startswith("Sent on") for _n, o in outs))
        loop.start()
        loop.inject("hello", kind="human")
        await settle(loop, lambda: loop.session.status == "idle" and len(client.calls) == 1)
        await asyncio.sleep(0.2)
        assert len(client.calls) == 1, "no second call just to read 'sent'"

        loop.inject("and another thing", kind="human")
        await settle(loop, lambda: len(client.calls) == 2)
        last = client.calls[-1][-1]
        assert isinstance(last, list) and last[0]["type"] == "tool_result", "the old results go in first"
        assert "and another thing" in last[-1]["text"]
        loop.request_stop()
        await loop.join()
        store.close()

    asyncio.run(run())


def test_without_the_rule_the_model_is_asked_again(tmp_path):
    async def run():
        client = Script(calls("reply"), DONE)
        loop, store = make_loop(tmp_path, client)
        loop.start()
        loop.inject("hello", kind="human")
        await settle(loop, lambda: len(client.calls) == 2)
        loop.request_stop()
        await loop.join()
        store.close()

    asyncio.run(run())


def test_a_busy_service_never_drops_the_message(tmp_path):
    async def run():
        client = Script(BUSY, BUSY, BUSY, DONE)
        loop, store = make_loop(tmp_path, client)
        told: list[bool] = []

        async def failed(_exc, again):
            told.append(again)

        loop.on_call_failed = failed
        loop.start()
        loop.inject("are you there?", kind="human")
        await settle(loop, lambda: len(client.calls) == 4)
        assert all("are you there?" in str(call) for call in client.calls), "the same message, every time"
        assert told == [True, True, True]
        loop.request_stop()
        await loop.join()
        store.close()

    asyncio.run(run())


def test_a_refused_request_is_given_up_after_three_tries_and_said(tmp_path):
    async def run():
        client = Script(REFUSED)
        loop, store = make_loop(tmp_path, client)
        told: list[bool] = []

        async def failed(_exc, again):
            told.append(again)

        loop.on_call_failed = failed
        loop.start()
        loop.inject("do it", kind="human")
        await settle(loop, lambda: len(told) == 3)
        await asyncio.sleep(0.3)
        assert len(client.calls) == 3 and told == [True, True, False], "bounded, and said"
        loop.request_stop()
        await loop.join()
        store.close()

    asyncio.run(run())


def test_passing_and_refusing_failures_are_told_apart():
    assert passing_failure(BUSY)
    assert passing_failure(ProviderError("gave up after 6 attempts: ConnectError: no route"))
    assert passing_failure(ProviderError("gave up after 6 attempts: HTTP 503: down"))
    assert not passing_failure(REFUSED)
    assert passing_failure(asyncio.TimeoutError())


def test_a_tool_loop_is_capped(tmp_path):
    async def run():
        client = Script(calls("look"))           # the model keeps calling a tool
        loop, store = make_loop(tmp_path, client, tools=("look",), role="watchdog")
        loop.max_rounds = 3
        loop.start()
        loop.inject("tick")
        await settle(loop, lambda: len(client.calls) == 3)
        await asyncio.sleep(0.3)
        assert len(client.calls) == 3
        loop.request_stop()
        await loop.join()
        store.close()

    asyncio.run(run())


def test_what_waits_survives_a_restart(tmp_path):
    async def run():
        store = Store(tmp_path / "t.db")
        first, _ = make_loop(tmp_path, Script(DONE), store=store, session_id="rtr_r")
        first.inject("message one", kind="human")
        first.inject("message two", kind="human")
        assert len(store.load_inbox("rtr_r")) == 2, "saved as soon as it arrives"

        # A new process: the same session, its saved conversation and inbox.
        client = Script(DONE)
        second, _ = make_loop(tmp_path, client, store=store, session_id="rtr_r")
        second.restore(store.load_context("rtr_r") or {"messages": []})
        second.start()
        await settle(second, lambda: len(client.calls) == 1)
        sent = str(client.calls[0])
        assert "message one" in sent and "message two" in sent
        await settle(second, lambda: store.load_inbox("rtr_r") == [])
        second.request_stop()
        await second.join()
        store.close()

    asyncio.run(run())


def test_the_persons_chat_goes_first():
    async def run():
        gate = PriorityGate(1)
        order: list[str] = []
        await gate.acquire(1)                    # someone holds the only slot

        async def wait(name, priority):
            async with gate.slot(priority):
                order.append(name)

        waiting = [asyncio.create_task(wait("watchdog", 2)), asyncio.create_task(wait("clone", 1))]
        await asyncio.sleep(0.01)
        waiting.append(asyncio.create_task(wait("rimuru", 0)))
        await asyncio.sleep(0.01)
        gate.release()
        await asyncio.gather(*waiting)
        assert order == ["rimuru", "clone", "watchdog"]

    asyncio.run(run())


def test_a_cancelled_waiter_does_not_keep_a_slot():
    async def run():
        gate = PriorityGate(1)
        await gate.acquire(0)
        waiter = asyncio.create_task(gate.acquire(1))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        gate.release()
        await asyncio.wait_for(gate.acquire(0), 1)

    asyncio.run(run())


def test_the_hourly_budget_stops_requests(monkeypatch):
    monkeypatch.setattr(BUDGET, "per_hour", 2)
    monkeypatch.setattr(METER, "_calls", {})
    check_budget("Kilo")
    METER.note("https://a")
    METER.note("https://b")
    with pytest.raises(ProviderAccountError) as caught:
        check_budget("Kilo")
    assert caught.value.reason == "budget" and "used up" in caught.value.plain()


def test_the_backup_model_takes_over():
    async def run():
        primary, backup = Script(BUSY), Script(DONE)
        client = FallbackClient(primary, backup, rest_seconds=60)
        client.stream = True
        assert (await client.complete(system="", messages=[], tools=[])).text == "ok"
        assert (await client.complete(system="", messages=[], tools=[])).text == "ok"
        assert len(primary.calls) == 1 and len(backup.calls) == 2, "it rests after failing"
        assert primary.stream is True and backup.stream is True

    asyncio.run(run())


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    return RimuruSystem(config, speed=50.0)


def answer_the_person(messages):
    """Reply on the thread the person wrote in."""
    found = re.findall(r"thr_[0-9a-z]+", str(messages[-1].content))
    return LLMResponse(text="", tool_calls=[ToolCall(id=new_id("step"), name="reply",
                                                     args={"thread_id": found[-1], "text": "Here I am."})])


def test_the_person_hears_once_and_then_gets_the_answer(tmp_path):
    async def run():
        system = make_system(tmp_path)
        client = Script(BUSY, BUSY, answer_the_person)
        system.router.loop.client = client
        system.router.loop.retry_base = 0.01
        await system.start()
        await system.person_says("hello?", thread_key="s-rel00001")
        lines = lambda: [m["text"] for m in system.store.chat_of("s-rel00001") if m["role"] == "rimuru"]  # noqa: E731
        await settle(system.router.loop, lambda: "Here I am." in lines(), seconds=8)
        notices = [t for t in lines() if "isn't answering" in t]
        assert len(notices) == 1, lines()
        assert "too many requests" in notices[0] and "safe" in notices[0]
        assert len(client.calls) == 3, "and no call after the reply just to read 'sent'"
        await system.stop()

    asyncio.run(run())


def test_the_watchdog_ends_its_tick_with_one_verdict(tmp_path):
    async def run():
        system = make_system(tmp_path)
        client = Script(LLMResponse(text="", tool_calls=[ToolCall(id="a1", name="assess",
                                                                    args={"verdict": "healthy", "note": "fine"})]))
        system.watchdog.loop.client = client
        system.watchdog.loop.start()
        system.watchdog.loop.inject("=== health report ===", kind="system")
        await settle(system.watchdog.loop, lambda: len(client.calls) == 1)
        await asyncio.sleep(0.3)
        assert len(client.calls) == 1, "it used to call itself 87 times"
        system.watchdog.loop.request_stop()
        await system.watchdog.loop.join()
        system.store.close()

    asyncio.run(run())


def test_a_job_past_its_time_limit_is_stopped(tmp_path, steady_demo):
    async def run():
        system = make_system(tmp_path)
        system.config.job_minutes = 1
        await system.start()
        thread = system.store.get_or_create_thread("console", "s-rel00002", "A page")
        task = system.store.put_task(Task(id=new_id("task"), title="Build a page", brief="b", thread_id=thread.id))
        await system.pool.submit(task, thread_id=thread.id)
        await settle(system.router.loop, lambda: task.status == "running", seconds=5)
        task.started_ts = time.time() - 120
        assert await system.pool.enforce_time_limit() == 1
        assert task.status == "cancelled" and "longer than 1 minutes" in task.result
        await system.stop()

    asyncio.run(run())
