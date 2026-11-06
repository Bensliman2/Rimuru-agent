"""Regression tests for the fixes the end-to-end evaluation called for
(docs/evaluation.md): lost updates, crash recovery, slow and stale replies."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.llm.mock import MockClient
from rimuru.models import R_ROUTER, Session, Task, Trigger
from rimuru.system import RimuruSystem
from rimuru.worker.session import WorkerAgent


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "sessions"
    return RimuruSystem(config, speed=50.0)


def clone(system: RimuruSystem, n: int) -> WorkerAgent:
    async def noop(*_a, **_k):
        return True

    session = system.store.put_session(
        Session(id=new_id("worker"), kind="worker", title=f"clone {n}", model="mock"))
    return WorkerAgent(task=Task(id=new_id("task"), title=f"clone {n}", brief=""),
                       session=session, store=system.store, bus=system.bus,
                       config=system.config, client=MockClient(), on_report=noop,
                       on_finish=noop, on_handback=noop, shell=system.shell)


# --------------------------------------------------------------------------- #
# P0: lost updates between clones
# --------------------------------------------------------------------------- #
def test_a_stale_whole_file_write_is_refused(tmp_path):
    """Regression: in the evaluation, one clone's whole-file write silently
    deleted a test another clone had just added."""
    async def run():
        system = make_system(tmp_path)
        ws = Path(system.config.workspace)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "t.py").write_text("original\n")
        a, b = clone(system, 1), clone(system, 2)
        va = (await a._tool_read_file({"path": "t.py"}, a.loop)).split()[1]
        vb = (await b._tool_read_file({"path": "t.py"}, b.loop)).split()[1]

        assert "Wrote" in await a._tool_write_file(
            {"path": "t.py", "content": "A's work\n", "base_version": va}, a.loop)
        stale = await b._tool_write_file(
            {"path": "t.py", "content": "B's work\n", "base_version": vb}, b.loop)
        assert stale.startswith("Refused")
        assert (ws / "t.py").read_text() == "A's work\n", "A's change was overwritten"
        # no version at all on an existing file is refused too
        assert (await b._tool_write_file({"path": "t.py", "content": "x"}, b.loop)).startswith("Refused")
        system.store.close()

    asyncio.run(run())


def test_targeted_edits_from_two_clones_both_land(tmp_path):
    async def run():
        system = make_system(tmp_path)
        ws = Path(system.config.workspace)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "t.py").write_text("x = 1\ny = 2\nz = 2\n")
        a, b = clone(system, 1), clone(system, 2)
        assert "Edited" in await a._tool_edit_file({"path": "t.py", "old": "x = 1", "new": "x = 10"}, a.loop)
        assert "Edited" in await b._tool_edit_file({"path": "t.py", "old": "y = 2", "new": "y = 20"}, b.loop)
        assert (ws / "t.py").read_text() == "x = 10\ny = 20\nz = 2\n"
        # ambiguous and missing text are refused, never guessed
        assert (await a._tool_edit_file({"path": "t.py", "old": "= 2", "new": "!"}, a.loop)).startswith("Refused")
        assert (await a._tool_edit_file({"path": "t.py", "old": "q = 9", "new": "!"}, a.loop)).startswith("Refused")
        system.store.close()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# P0: state survives a restart, and interrupted work is recovered
# --------------------------------------------------------------------------- #
def test_records_memory_and_chat_survive_a_restart(tmp_path):
    system = make_system(tmp_path)
    store = system.store
    task = store.put_task(Task(id=new_id("task"), title="a job", brief="b", status="done"))
    store.add_note("the owner prefers short replies")
    store.add_chat("user", "hi")
    thread = store.get_or_create_thread("console", "direct", "direct line")
    store.close()

    again = make_system(tmp_path).store
    assert again.tasks[task.id].title == "a job"
    assert again.notes[-1]["note"] == "the owner prefers short replies"
    assert again.thread_by_key["console:direct"] == thread.id
    assert again.chat[-1]["text"] == "hi"
    again.close()


# Crash and restart recovery: see tests/test_resume.py.


# --------------------------------------------------------------------------- #
# P1: faster, fresher replies
# --------------------------------------------------------------------------- #
def test_direct_messages_skip_the_semantic_filter(tmp_path):
    """Regression: every chat message paid ~5s for a model call asking whether
    the person's own message was noise - and long ones were trimmed."""
    async def run():
        system = make_system(tmp_path)
        await system.start()
        trigger = await system.human_message("x" * 1000 + " please build a thing")
        assert system.filter.counters["slow_calls"] == 0
        assert trigger.route == R_ROUTER
        await system.stop()

    asyncio.run(run())


def test_a_reply_is_held_if_the_person_wrote_again(tmp_path):
    """Regression: "I'll create the manual" went out after the person had
    already said "cancel the manual"."""
    async def run():
        system = make_system(tmp_path)
        loop = system.router.loop
        thread = system.store.get_or_create_thread("console", "direct", "direct line")
        loop.turn_started_ts = time.time()
        await asyncio.sleep(0.01)
        newer = Trigger(id=new_id("trigger"), channel="console", thread_key="direct",
                        author="you", kind="human_direct", content="actually, cancel that")
        system.store.put_trigger(newer)
        system.store.attach_trigger_to_thread(thread, newer)

        held = await system.router._tool_reply({"thread_id": thread.id, "text": "Writing it now"}, loop)
        assert held.startswith("Not sent") and "cancel that" in held
        assert system.channels.outbox == []

        loop.turn_started_ts = time.time()                     # a turn that has seen it
        sent = await system.router._tool_reply({"thread_id": thread.id, "text": "Cancelled."}, loop)
        assert sent.startswith("Sent")
        system.store.close()

    asyncio.run(run())


def test_an_added_request_does_not_hold_a_reply_that_is_still_true(tmp_path):
    """Refinement from the re-run: holding every reply when the person wrote again
    delayed the first answer by 25s during a burst of new requests. Only messages
    that change course hold a reply; additions let it through."""
    async def run():
        system = make_system(tmp_path)
        loop = system.router.loop
        thread = system.store.get_or_create_thread("console", "direct", "direct line")
        loop.turn_started_ts = time.time()
        await asyncio.sleep(0.01)
        added = Trigger(id=new_id("trigger"), channel="console", thread_key="direct",
                        author="you", kind="human_direct", content="We also need coupon codes")
        system.store.put_trigger(added)
        system.store.attach_trigger_to_thread(thread, added)

        sent = await system.router._tool_reply({"thread_id": thread.id, "text": "On it."}, loop)
        assert sent.startswith("Sent") and "written again" in sent
        assert len(system.channels.outbox) == 1
        system.store.close()

    asyncio.run(run())


def test_a_relayed_message_is_kept_in_the_record_after_long_tool_output(tmp_path):
    """Third evaluation run: Rimuru relayed the coupon request while the clone was
    reading a long file. The model got it, but the step record was cut at 4000
    characters, so the details page and recall never showed it."""
    from rimuru.llm.base import LLMResponse, ToolCall

    async def run():
        system = make_system(tmp_path)
        ws = Path(system.config.workspace)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "big.py").write_text("x = 1\n" * 3000)
        worker = clone(system, 1)

        class OneLongRead(MockClient):
            calls = 0

            async def complete(self, **kwargs):
                OneLongRead.calls += 1
                if OneLongRead.calls == 1:
                    # Rimuru relays a follow-up while this turn is still running.
                    worker.loop.inject("Relayed by the Router:\nAlso add coupon codes.", kind="human")
                    return LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file",
                                                            args={"path": "big.py"})])
                return LLMResponse(text="Noted.")

        worker.loop.client = OneLongRead()
        worker.loop.start()                           # the brief is already queued
        deadline = time.time() + 5
        steps = []
        while time.time() < deadline and len(steps) < 2:
            await asyncio.sleep(0.05)
            steps = system.store.transcript(worker.session.id)
        assert len(steps) >= 2
        assert "Also add coupon codes." in steps[1].injected
        assert "more characters not kept" in steps[1].injected   # the long read was clipped
        worker.loop.request_stop("stopped")
        await worker.loop.join()
        system.store.close()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Third run: a person's message waited 25s behind a turn nobody needed
# --------------------------------------------------------------------------- #
def _router_with(system, client):
    system.router.loop.client = client
    return system.router.loop


def test_a_chat_message_cuts_in_on_a_turn_about_something_else(tmp_path):
    from rimuru.llm.base import LLMResponse

    async def run():
        system = make_system(tmp_path)
        calls = []

        class SlowOnReports(MockClient):
            async def complete(self, *, messages, **kwargs):
                calls.append((time.time(), messages[-1].as_text()))
                if len(calls) == 1:
                    await asyncio.sleep(30)        # a long think about a clone's report
                return LLMResponse(text="ok")

        loop = _router_with(system, SlowOnReports())
        await system.start()
        loop.inject("[clone report] the bulk discount is done", kind="event")
        while not calls:
            await asyncio.sleep(0.01)
        asked = time.time()
        await system.human_message("How is everything going?")
        while len(calls) < 2 and time.time() - asked < 5:
            await asyncio.sleep(0.01)

        assert len(calls) == 2, "the person's message waited behind the report"
        assert calls[1][0] - asked < 2.0
        assert "the bulk discount is done" in calls[1][1], "the report was lost"
        assert "How is everything going?" in calls[1][1]
        assert loop.cut_ins == 1
        await system.stop()

    asyncio.run(run())


def test_a_turn_the_person_started_is_never_cut_short(tmp_path):
    from rimuru.llm.base import LLMResponse

    async def run():
        system = make_system(tmp_path)
        calls = []

        class Slow(MockClient):
            async def complete(self, *, messages, **kwargs):
                calls.append(messages[-1].as_text())
                await asyncio.sleep(0.6)
                return LLMResponse(text="ok")

        loop = _router_with(system, Slow())
        await system.start()
        await system.human_message("Please add a 15% tier for 50+ items")
        while not calls:
            await asyncio.sleep(0.01)
        await system.human_message("And coupon codes too")
        await asyncio.sleep(0.3)
        assert len(calls) == 1 and loop.cut_ins == 0, "a person's own turn was restarted"
        await system.stop()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Speed: a first version first - the only way clones build
# --------------------------------------------------------------------------- #
def test_clones_always_build_a_first_version_first(tmp_path):
    system = make_system(tmp_path)
    assert "A FIRST VERSION FIRST" in clone(system, 1).loop.system_prompt
    assert "SPEED" in system.router.loop.system_prompt
    system.store.close()


def test_an_answer_written_but_not_sent_still_reaches_the_person(tmp_path):
    """Found with a free model: it answered in plain text instead of calling
    reply, so the person saw nothing - not the answer to their question, not
    "your calculator is ready". The app sends that text itself, to the one
    session waiting on it - and never routine notes, never twice."""
    from rimuru.llm.base import LLMResponse, ToolCall

    async def until(cond, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end and not cond():
            await asyncio.sleep(0.02)
        return cond()

    async def run():
        system = make_system(tmp_path)
        store = system.store

        class TextOnly(MockClient):
            async def complete(self, *, messages, **kwargs):
                last = messages[-1].as_text()
                if "Sent on" in last:
                    return LLMResponse(text="Replied to them.")
                if "reports status=" in last:
                    return LLMResponse(text="No action needed - routine progress.")
                if "finished as done" in last:
                    return LLMResponse(text="Your calculator is ready!")
                if '"thanks!"' in last:
                    return LLMResponse(tool_calls=[ToolCall(id="r1", name="reply", args={
                        "thread_id": "console", "text": "You're welcome!"})])
                if "15% tip" in last:
                    return LLMResponse(text="15% of $64 is **$9.60**.")
                return LLMResponse(text="ok")

        _router_with(system, TextOnly())
        await system.start()

        def said():
            return [m["text"] for m in store.chat if m.get("chat") == "s-0001" and m["role"] == "rimuru"]

        await system.human_message("quick question, a 15% tip on 64 dollars?", thread_key="s-0001")
        assert await until(lambda: said() == ["15% of $64 is **$9.60**."]), said()

        thread = next(t for t in store.threads.values() if t.key == "s-0001")
        worker = clone(system, 1)
        worker.task.thread_id = thread.id
        steps = lambda: len(store.transcript(system.router.session.id))  # noqa: E731
        before = steps()
        await system.pool._on_report(worker, "progressing", "Starting on the calculator.")
        assert await until(lambda: steps() > before)
        await asyncio.sleep(0.2)
        assert len(said()) == 1, said()                      # routine progress: nothing sent

        await system.pool._on_finish(worker, "done", "Built index.html")
        assert await until(lambda: len(said()) == 2), said()
        assert said()[1] == "Your calculator is ready!"

        await system.human_message("thanks!", thread_key="s-0001")
        assert await until(lambda: len(said()) == 3), said()
        await asyncio.sleep(0.3)
        assert said()[2:] == ["You're welcome!"]           # sent by reply, nothing doubled
        await system.stop()

    asyncio.run(run())
