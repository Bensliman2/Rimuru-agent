"""Tests for the mechanisms the article's architecture depends on.

Each test pins down one behaviour that is easy to get subtly wrong - several of
them are regressions for bugs found while building this.

    python -m pytest -q
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rimuru.config import Config
from rimuru.context import trim_messages
from rimuru.ids import new_id
from rimuru.llm.base import Msg
from rimuru.models import R_PASSTHROUGH, R_ROUTER, V_DROPPED_FAST, Trigger
from rimuru.system import RimuruSystem


def make_system(tmp_path: Path, **overrides) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "test.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "sessions"
    for key, value in overrides.items():
        setattr(config, key, value)
    return RimuruSystem(config, speed=50.0)


def trig(content: str, *, channel: str = "im", key: str = "#bugs/topic-1",
         author: str = "alice", kind: str = "message", **meta) -> Trigger:
    return Trigger(id=new_id("trigger"), channel=channel, thread_key=key,
                   author=author, kind=kind, content=content, meta=meta)


async def wait_for(predicate, timeout: float = 15.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


# --------------------------------------------------------------------------- #
# Inline & Spill: trimming must never orphan a tool_result
# --------------------------------------------------------------------------- #
def test_trim_never_starts_tail_on_orphaned_tool_result():
    messages = [Msg("user", "brief " * 50)]
    for i in range(60):
        messages.append(Msg("assistant", [
            {"type": "text", "text": "x" * 200},
            {"type": "tool_use", "id": f"t{i}", "name": "start", "input": {}},
        ]))
        messages.append(Msg("user", [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "y" * 200},
        ]))

    trimmed, dropped = trim_messages(messages, budget_chars=6000, keep_tail=11)
    assert dropped > 0
    # head, then the spill marker, then a tail that is legal to send to an API
    after_marker = trimmed[2:]
    first = after_marker[0]
    assert not (
        first.role == "user"
        and isinstance(first.content, list)
        and any(b.get("type") == "tool_result" for b in first.content)
    ), "tail opens on a tool_result whose tool_use was dropped"
    # and the head must not carry a tool_use whose result was dropped
    assert trimmed[0].role == "user" and isinstance(trimmed[0].content, str)


def test_trim_leaves_short_transcripts_alone():
    messages = [Msg("user", "hi"), Msg("assistant", "hello")]
    trimmed, dropped = trim_messages(messages, budget_chars=10_000)
    assert dropped == 0 and trimmed == messages


# --------------------------------------------------------------------------- #
# Fast Path filtering: deterministic, no model call
# --------------------------------------------------------------------------- #
def test_fast_path_drops_green_ci_and_own_messages(tmp_path):
    system = make_system(tmp_path)
    f = system.filter
    assert f.fast_path(trig("build passed", kind="ci", status="success"))[0] == V_DROPPED_FAST
    assert f.fast_path(trig("anything", author="rimuru"))[0] == V_DROPPED_FAST
    assert f.fast_path(trig(""))[0] == V_DROPPED_FAST
    assert f.fast_path(trig("build failed", kind="ci", status="failure"))[0] != V_DROPPED_FAST
    system.store.close()


def test_semantic_filter_only_drops_on_an_explicit_ignore():
    """Regression, found against a real reasoning model (Kimi k3.1): its
    thinking used the whole token budget, the answer came back empty, and a
    genuine bug report was silently dropped as 'no action needed'."""
    from rimuru.models import V_ACCEPTED, V_DROPPED_SLOW
    from rimuru.router.filtering import parse_verdict

    assert parse_verdict("")[0] == V_ACCEPTED
    assert parse_verdict(None)[0] == V_ACCEPTED
    assert parse_verdict("I think this one is about...")[0] == V_ACCEPTED
    assert parse_verdict("**ACT**: a bug report")[0] == V_ACCEPTED
    assert parse_verdict("ACT: a bug report") == (V_ACCEPTED, "a bug report")
    assert parse_verdict("IGNORE: social chatter") == (V_DROPPED_SLOW, "social chatter")
    assert parse_verdict("Verdict below\n**IGNORE** - lunch plans")[0] == V_DROPPED_SLOW


def test_mention_only_channel_needs_an_explicit_request(tmp_path):
    system = make_system(tmp_path)
    system.filter.set_channel_mode("im", "mention_only")
    assert system.filter.fast_path(trig("the exporter crashes"))[0] == V_DROPPED_FAST
    assert system.filter.fast_path(trig("@rimuru the exporter crashes"))[0] != V_DROPPED_FAST
    system.store.close()


# --------------------------------------------------------------------------- #
# Routing: subscription and pass-through
# --------------------------------------------------------------------------- #
def test_followups_pass_through_to_the_owning_worker(tmp_path):
    async def run():
        system = make_system(tmp_path)
        await system.start()

        first = trig("the CSV exporter crashes on an empty result set")
        await system.ingest(first)
        assert first.route == R_ROUTER

        # The Router dispatches, and the worker subscribes to the thread.
        thread_id = first.thread_id
        assert await wait_for(lambda: system.store.subscribers_of(thread_id))

        follow = trig("I can reproduce it locally if that helps")
        await system.ingest(follow)
        assert follow.route == R_PASSTHROUGH
        assert follow.routed_to == system.store.subscribers_of(thread_id)
        # It went to the worker, not into the Router's window - but the Router
        # can still find it.
        assert follow.id in system.store.router_spill
        await system.stop()

    asyncio.run(run())


def test_a_new_topic_in_the_same_channel_still_reaches_the_router(tmp_path):
    """Regression: modelling a whole channel as one Thread swallowed new requests."""
    async def run():
        system = make_system(tmp_path)
        await system.start()

        a = trig("the CSV exporter crashes on an empty result set", key="#bugs/csv-1")
        await system.ingest(a)
        assert await wait_for(lambda: system.store.subscribers_of(a.thread_id))

        b = trig("parser drops the last row - a bug", key="#bugs/parser-2")
        await system.ingest(b)
        assert b.route == R_ROUTER, "a new topic must not pass through to another topic's worker"
        assert b.thread_id != a.thread_id
        await system.stop()

    asyncio.run(run())


def test_scenarios_give_each_request_its_own_topic():
    """Regression: the generator used one Thread per channel, so the first request
    in a channel captured the whole channel and later requests were swallowed."""
    from rimuru.channels.scenario import build

    for name in ("team", "burst", "quiet"):
        events = build(name).events
        request_keys = [e.thread_key for e in events if e.meta.get("intent") == "work_request"]
        chatter_keys = {e.thread_key for e in events if e.meta.get("intent") == "chatter"}
        assert request_keys, name
        assert len(set(request_keys)) == len(request_keys), f"{name}: requests share a topic"
        assert not set(request_keys) & chatter_keys, f"{name}: a request shares chatter's topic"


# --------------------------------------------------------------------------- #
# Admission control: over the limit, tasks queue rather than vanish
# --------------------------------------------------------------------------- #
def test_tasks_over_the_worker_limit_are_queued_not_dropped(tmp_path):
    async def run():
        system = make_system(tmp_path, max_workers=1)
        await system.start()
        for i in range(3):
            await system.ingest(trig(f"bug number {i}: the thing crashes", key=f"#bugs/t-{i}"))

        assert await wait_for(lambda: len(system.store.tasks) == 3)
        statuses = sorted(t.status for t in system.store.tasks.values())
        assert statuses.count("running") <= 1
        assert "queued" in statuses
        # and the backlog drains as workers finish
        assert await wait_for(
            lambda: all(t.status in {"done", "failed"} for t in system.store.tasks.values()),
            timeout=40,
        )
        await system.stop()

    asyncio.run(run())


def test_queued_worker_reads_its_brief_before_followups(tmp_path, monkeypatch):
    """Regression: a queued worker got 'any update?' before its own task brief,
    answered the status question, and never started the work."""
    import random

    # The offline stand-in fails 12% of pretend commands on purpose, and gives up
    # after two - which made this test about ORDER fail now and then. No dice here.
    monkeypatch.setattr(random, "random", lambda: 0.99)

    async def run():
        system = make_system(tmp_path, max_workers=1)
        await system.start()
        await system.ingest(trig("the CSV exporter crashes", key="#bugs/a"))
        b = trig("the parser crashes on unicode", key="#bugs/b")
        await system.ingest(b)
        assert await wait_for(lambda: any(
            t.status == "queued" for t in system.store.tasks.values()))

        # A follow-up reaches the queued worker while it waits for a slot.
        await system.ingest(trig("any update on that?", key="#bugs/b"))

        task_b = next(t for t in system.store.tasks.values() if t.thread_id == b.thread_id)
        assert await wait_for(lambda: task_b.status in {"done", "failed"}, timeout=40)
        assert task_b.status == "done", task_b.result
        first = system.store.transcript(task_b.worker_id)[0].injected
        assert first.find("Your task is") < first.find("New event on") or \
            "New event on" not in first
        await system.stop()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# stop_task resolves by thread, never by "latest"
# --------------------------------------------------------------------------- #
def test_cancellation_stops_the_task_on_that_thread_only(tmp_path):
    async def run():
        system = make_system(tmp_path)
        await system.start()
        a = trig("the CSV exporter crashes", key="#bugs/a")
        b = trig("the parser crashes too", key="#bugs/b")
        await system.ingest(a)
        await system.ingest(b)
        assert await wait_for(lambda: len(system.store.tasks) == 2)

        task_a = next(t for t in system.store.tasks.values() if t.thread_id == a.thread_id)
        task_b = next(t for t in system.store.tasks.values() if t.thread_id == b.thread_id)
        result = await system.router._tool_stop_task(
            {"thread_id": a.thread_id, "reason": "withdrawn"}, system.router.loop
        )
        assert task_a.id in result
        assert task_a.status == "cancelled"
        assert task_b.status != "cancelled"

        # "latest" is no longer a magic value that can hit the wrong task
        result = await system.router._tool_stop_task(
            {"task_id": "latest", "reason": "x"}, system.router.loop
        )
        assert "No task" in result
        await system.stop()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# reply never guesses a thread
# --------------------------------------------------------------------------- #
def test_reply_with_unknown_thread_sends_nothing(tmp_path):
    async def run():
        system = make_system(tmp_path)
        await system.start()
        result = await system.router._tool_reply(
            {"thread_id": "thr_doesnotexist", "text": "hello"}, system.router.loop
        )
        assert "Nothing was sent" in result
        assert system.channels.outbox == []
        await system.stop()

    asyncio.run(run())


def test_im_replies_are_capped_at_100_words(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread = system.store.get_or_create_thread("im", "#bugs/x", "x")
        result = await system.router._tool_reply(
            {"thread_id": thread.id, "text": "word " * 150}, system.router.loop
        )
        sent = system.channels.outbox[-1]
        assert sent.truncated and len(sent.text.split()) <= 101
        assert "capped" in result
        system.store.close()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Handback: a worker returns an event that is not its own
# --------------------------------------------------------------------------- #
def test_handback_reroutes_through_the_router(tmp_path):
    async def run():
        system = make_system(tmp_path)
        await system.start()
        a = trig("the CSV exporter crashes", key="#bugs/a")
        await system.ingest(a)
        assert await wait_for(lambda: system.store.subscribers_of(a.thread_id))
        worker = system.pool.get(system.store.subscribers_of(a.thread_id)[0])

        stray = trig("totally separate: webhook signature check fails", key="#bugs/a")
        await system.ingest(stray)
        ok = await system.pool.handback(worker, stray.id, "not mine")
        assert ok and system.pool.handbacks == 1
        assert system.router.session.id in stray.routed_to
        await system.stop()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Full duplex: start() returns before the job finishes
# --------------------------------------------------------------------------- #
def test_start_returns_immediately_and_result_arrives_as_an_event(tmp_path):
    async def run():
        system = make_system(tmp_path)
        await system.start()
        a = trig("the CSV exporter crashes", key="#bugs/a")
        await system.ingest(a)
        assert await wait_for(lambda: system.store.jobs)
        job = next(iter(system.store.jobs.values()))
        worker = system.pool.get(job.session_id)

        # The tool call returned while the job was still running...
        started = [r for s in system.store.transcript(worker.session.id)
                   for r in s.tool_results if r["name"] == "start"]
        assert started and "running in the background" in started[0]["output"]
        # ...and the result later came back in as an injected event.
        assert await wait_for(lambda: job.status != "running")
        assert await wait_for(lambda: any(
            "job finished" in s.injected.lower() or "job failed" in s.injected.lower()
            for s in system.store.transcript(worker.session.id)
        ))
        await system.stop()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Batching: events that queue during a Router turn are merged into the next
# --------------------------------------------------------------------------- #
def test_router_merges_events_that_queue_during_a_turn(tmp_path):
    async def run():
        system = make_system(tmp_path)
        await system.start()
        # Occupy the router, then pile events up behind it.
        system.router.inject("warm-up event, ignore it")
        await asyncio.sleep(0.05)
        for i in range(5):
            system.router.inject(f"queued event {i}")
        assert await wait_for(
            lambda: any(s.batch_size >= 3 for s in system.store.transcript("rtr_main"))
        )
        await system.stop()

    asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_text_for_people_has_no_internal_ids_or_channel_jargon():
    """Found in the third evaluation run: the details page showed the Router's
    brief verbatim - 'on the console direct chat' and 'Thread: thr_c0a27b4e.'"""
    from rimuru.web.friendly import plain

    brief = ("A person on the console direct chat asked: \u201cAdd a 15% tier.\u201d "
             "Run the tests and report back. Thread: thr_c0a27b4e.")
    assert plain(brief) == ("A person in the chat asked: \u201cAdd a 15% tier.\u201d "
                            "Run the tests and report back.")
    assert plain("Relay it to worker wrk_a9887aab (task tsk_30a3fc49) now") == "Relay it to now"
    assert plain('The user asked in the direct console: "Write a tutorial."') == (
        'The user asked in the chat: "Write a tutorial."')
    assert plain("SAVE10 now takes 20% off; 15 passed") == "SAVE10 now takes 20% off; 15 passed"
