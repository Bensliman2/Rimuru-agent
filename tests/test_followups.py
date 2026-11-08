"""Faster, cheaper jobs: the clone tells the person itself (one AI call saved), a
job on the heels of the last one goes to the clone that just finished it, new
clones hear what earlier ones did, and the free limit is warned about before it
stops the work. Offline."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from rimuru import skills
from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.llm.base import METER, RequestMeter
from rimuru.models import Step, Task
from rimuru.system import RimuruSystem
from rimuru.web import friendly

CONV = frozenset({"console", "telegram"})


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    config.max_workers = 0          # the clones wait: these tests drive them by hand
    return RimuruSystem(config, speed=50.0)


async def clone_for(system, key="s-foll0001", title="Build a page", **task):
    thread = system.store.get_or_create_thread("console", key, "A page")
    made = system.store.put_task(Task(id=new_id("task"), title=title, brief="the brief", thread_id=thread.id, **task))
    session, _ = await system.pool.submit(made, thread_id=thread.id)
    return thread, made, session, system.pool.workers[session.id]


def finished_conversation(session_id: str, system) -> None:
    """What a clone's saved conversation looks like when it called finish()."""
    system.store.save_context(session_id, {"pending": None, "messages": [
        {"role": "user", "content": "Your task is to build a page"},
        {"role": "assistant", "content": [{"type": "text", "text": "All done."},
                                          {"type": "tool_use", "id": "call_finish", "name": "finish", "input": {}}]},
    ]})


# ---------------------------------------------------------------------- the clone's own last word
def test_the_clone_tells_the_person_itself_and_rimuru_spends_no_turn(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread, task, session, worker = await clone_for(system)
        await system.pool._on_finish(worker, "done", "Built index.html.", "Your page is ready - open index.html.")

        line = system.store.chat[-1]
        assert line["role"] == "rimuru" and line["text"] == "Your page is ready - open index.html."
        assert line["chat"] == "s-foll0001" and line["clone"] == session.name and line["worker_id"] == session.id
        assert line["color"] and line["edge"]
        assert system.router.loop.inbox.qsize() == 0, "Rimuru must not spend a turn on it"
        assert system.store.tasks[task.id].status == "done"

        # Rimuru hears of it with its next turn - and that note survives a restart.
        assert "already told the person" in system.router.loop._notes[-1]
        assert system.store.load_notes(system.router.session.id) == system.router.loop._notes
        await system.start()
        system.router.inject("Something else happened.")
        end = time.time() + 10
        while time.time() < end and not system.store.transcript(system.router.session.id):
            await asyncio.sleep(0.02)
        first = system.store.transcript(system.router.session.id)[0]
        assert first.injected.startswith("Since your last turn:") and "Built index.html." in first.injected
        assert "Something else happened." in first.injected and not system.router.loop._notes
        await system.stop()

    asyncio.run(run())


def test_a_job_that_failed_is_explained_by_its_clone_too(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _thread, _task, _session, worker = await clone_for(system)
        await system.pool._on_finish(worker, "failed", "Could not install it.", "I could not finish: the disk is full.")
        assert system.store.chat[-1]["text"] == "I could not finish: the disk is full."
        assert system.router.loop.inbox.qsize() == 0
        await system.stop()

    asyncio.run(run())


def test_without_its_message_rimuru_tells_the_person_as_before(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _thread, _task, _session, worker = await clone_for(system)
        await system.pool._on_finish(worker, "done", "Built index.html.", "")
        news = system.router.loop.inbox.get_nowait().text
        assert "finished as done" in news and "Tell whoever asked" in news
        assert not system.router.loop._notes
        await system.stop()

    asyncio.run(run())


def test_work_outside_a_chat_still_goes_through_rimuru(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread = system.store.get_or_create_thread("im", "#team/x", "A thread")
        task = system.store.put_task(Task(id=new_id("task"), title="Fix it", brief="b", thread_id=thread.id))
        session, _ = await system.pool.submit(task, thread_id=thread.id)
        await system.pool._on_finish(system.pool.workers[session.id], "done", "Fixed.", "It is fixed.")
        assert "finished as done" in system.router.loop.inbox.get_nowait().text, "a team chat has its own manners"
        await system.stop()

    asyncio.run(run())


def test_the_brief_asks_for_that_message_in_the_persons_language(tmp_path):
    from rimuru.models import Trigger

    async def run():
        system = make_system(tmp_path)
        thread = system.store.get_or_create_thread("console", "s-lang0001", "Une page")
        trigger = system.store.put_trigger(Trigger(id=new_id("trigger"), channel="console", thread_key="s-lang0001",
                                                   author="you", kind="human_direct", content="Crée une page pour mon café",
                                                   thread_id=thread.id))
        task = system.store.put_task(Task(id=new_id("task"), title="Build a page", brief="b", thread_id=thread.id,
                                          origin_trigger_id=trigger.id))
        session, _ = await system.pool.submit(task, thread_id=thread.id)
        worker = system.pool.workers[session.id]
        brief = worker.loop.inbox.get_nowait().text
        assert "pass for_person to finish()" in brief
        assert 'SAME LANGUAGE as their message, which was: "Crée une page pour mon café"' in brief
        assert any("for_person" in str(spec) for spec in worker.registry.specs())
        await system.stop()

    asyncio.run(run())


# ---------------------------------------------------------------------- follow-ups
def dispatch(system, thread, **args):
    turn = SimpleNamespace(turn_events=[], messages=[])
    return system.router._tool_dispatch({"title": "Make it blue", "brief": "Make the title blue.",
                                         "thread_id": thread.id, **args}, turn)


def test_a_job_soon_after_goes_to_the_clone_that_just_finished(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread, task, first, worker = await clone_for(system)
        finished_conversation(first.id, system)
        await system.pool._on_finish(worker, "done", "Built it.", "Ready.")
        await system.pool._on_session_finished({"session_id": first.id})      # as when its loop ends
        assert system.store.load_context(first.id), "kept for a follow-up"

        said = await dispatch(system, thread)
        assert "the same clone that just finished" in said
        follow = next(t for t in system.store.tasks.values() if t.title == "Make it blue")
        clone = system.store.sessions[follow.worker_id]
        assert clone.id != first.id and (clone.name, clone.persona) == (first.name, first.persona), "the same clone"
        again = system.pool.workers[clone.id]
        assert [m.role for m in again.loop.messages] == ["user", "assistant"], "it remembers its conversation"
        assert again.loop._restored_pending == [{"type": "tool_result", "tool_use_id": "call_finish",
                                                 "content": "Done - that job is closed."}]
        note = again.loop.inbox.get_nowait().text
        assert note.startswith("A FOLLOW-UP, FOR YOU") and "Make the title blue." in note and follow.id in note
        assert "pass for_person to finish()" in note, "the follow-up gets told how to finish too"
        assert system.store.load_context(first.id) is None, "one follow-up carries it on"

        # Its old job keeps its own card and result.
        assert system.store.tasks[task.id].result == "Built it." and system.store.sessions[first.id].status == "done"

        # The conversation has been taken: another job at the same moment gets a new clone.
        said = await dispatch(system, thread, title="Another")
        assert "the same clone" not in said
        await system.stop()

    asyncio.run(run())


def test_after_ten_minutes_or_when_asked_a_new_clone_starts(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread, _task, first, worker = await clone_for(system)
        finished_conversation(first.id, system)
        await system.pool._on_finish(worker, "done", "Built it.", "Ready.")

        assert "the same clone" not in await dispatch(system, thread, fresh="true"), "fresh: a new clone"
        assert system.store.load_context(first.id), "and the finished one stays available"
        task = system.store.tasks[first.task_id]
        system.store.update_task(task, finished_ts=time.time() - system.config.warm_clone_seconds - 1)
        assert system.pool.warm_clone(thread.id) is None, "too long ago"
        assert "the same clone" not in await dispatch(system, thread, title="Later")
        await system.stop()

    asyncio.run(run())


def test_a_forced_skill_goes_along_on_a_follow_up(tmp_path):
    from rimuru.models import Trigger

    async def run():
        system = make_system(tmp_path)
        skills.save(system.config.skills_dir, name="Web pages", text="- Check it at 375px wide")
        thread, _task, first, worker = await clone_for(system)
        finished_conversation(first.id, system)
        await system.pool._on_finish(worker, "done", "Built it.", "Ready.")
        trigger = system.store.put_trigger(Trigger(id=new_id("trigger"), channel="console", thread_key=thread.key,
                                                   author="you", kind="human_direct", content="make it blue",
                                                   thread_id=thread.id, meta={"skills": ["Web pages"]}))
        turn = SimpleNamespace(turn_events=[SimpleNamespace(trigger_id=trigger.id)], messages=[])
        await system.router._tool_dispatch({"title": "Make it blue", "brief": "b", "thread_id": thread.id}, turn)
        follow = next(t for t in system.store.tasks.values() if t.title == "Make it blue")
        assert "375px" in system.pool.workers[follow.worker_id].loop.inbox.get_nowait().text
        assert follow.origin_trigger_id == trigger.id
        await system.stop()

    asyncio.run(run())


def test_finished_conversations_are_kept_only_as_long_as_they_can_be_used(tmp_path):
    async def run():
        system = make_system(tmp_path)
        store = system.store
        thread, _task, first, worker = await clone_for(system)
        finished_conversation(first.id, system)
        await system.pool._on_finish(worker, "done", "Built it.", "Ready.")
        _t, _tk, failed, broken = await clone_for(system, key="s-foll0002")
        finished_conversation(failed.id, system)
        await system.pool._on_finish(broken, "failed", "No.", "It failed.")
        assert store.load_context(failed.id) is None, "nothing to continue"

        _t, _tk, running, _w = await clone_for(system, key="s-foll0003")
        finished_conversation(running.id, system)
        assert system.pool.sweep_warm() == 0, "still within the window"
        store.touch_session(first, status="done")
        first.updated_ts = time.time() - system.config.warm_clone_seconds - 1
        assert system.pool.sweep_warm() == 1 and store.load_context(first.id) is None
        assert store.load_context(running.id), "a clone at work keeps its conversation"
        await system.stop()

    asyncio.run(run())


# ---------------------------------------------------------------------- what earlier clones did
def test_a_new_clone_hears_what_earlier_ones_did(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread, task, first, worker = await clone_for(system)
        system.store.add_step(Step(id=new_id("step"), session_id=first.id, index=0, tool_calls=[
            {"id": "c", "name": "write_file", "args": {"path": "index.html", "content": "x"}}]))
        await system.pool._on_finish(worker, "done", "Built the page with a form.", "Ready.")

        made = system.store.put_task(Task(id=new_id("task"), title="Add a footer", brief="b", thread_id=thread.id))
        session, _ = await system.pool.submit(made, thread_id=thread.id)
        brief = system.pool.workers[session.id].loop.inbox.get_nowait().text
        assert "EARLIER IN THIS SESSION" in brief and f'[{task.id}] "Build a page"' in brief
        assert "Built the page with a form." in brief and "Files: index.html." in brief and first.name in brief

        other = system.store.get_or_create_thread("console", "s-othr0001", "Other")
        elsewhere = system.store.put_task(Task(id=new_id("task"), title="Else", brief="b", thread_id=other.id))
        session, _ = await system.pool.submit(elsewhere, thread_id=other.id)
        assert "EARLIER IN THIS SESSION" not in system.pool.workers[session.id].loop.inbox.get_nowait().text
        await system.stop()

    asyncio.run(run())


def test_a_clones_card_says_what_its_job_cost(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _thread, _task, session, _worker = await clone_for(system)
        step = Step(id=new_id("step"), session_id=session.id, index=0)
        step.usage.input_tokens, step.usage.output_tokens = 1200, 300
        system.store.add_step(step)
        card = friendly.worker_card(system.store, session, CONV)
        assert card["turns"] == 1 and card["tokens"] == 1500
        await system.stop()

    asyncio.run(run())


# ---------------------------------------------------------------------- the free limit
def test_requests_are_counted_within_the_hour():
    meter = RequestMeter()
    meter.note("https://x.test/")
    meter.note("https://x.test")
    assert meter.used("https://x.test") == 2
    assert meter.used("https://x.test", within=-1) == 0, "older than the window: forgotten"
    assert meter.used("https://other.test") == 0


def test_the_person_is_warned_before_the_free_limit_not_after(tmp_path):
    async def run():
        system = make_system(tmp_path)
        system.config.is_demo = lambda: False
        system.config.setup = SimpleNamespace(entry_for=lambda _role: SimpleNamespace(provider="kilo", base_url=""))
        url = "https://api.kilo.ai/api/gateway"
        METER._calls.pop(url, None)
        for _ in range(100):
            METER.note(url)
        assert system.quota() == [{"provider": "Kilo Code (free, no key)", "limit": 200, "used": 100}]
        system.store.get_or_create_thread("console", "s-limt0001", "A page")
        system.store.add_chat("user", "hello", chat="s-limt0001")
        system.warn_about_quota()
        assert system.store.chat[-1]["role"] == "user", "only half used: nothing to say"
        for _ in range(70):
            METER.note(url)
        system.warn_about_quota()
        line = system.store.chat[-1]
        assert line["notice"] == "quota" and "170 of about 200" in line["text"] and line["chat"] == "s-limt0001"
        count = len(system.store.chat)
        system.warn_about_quota()
        assert len(system.store.chat) == count, "said once, not every minute"
        METER._calls.pop(url, None)
        system.config.is_demo = lambda: True
        await system.stop()

    asyncio.run(run())


def test_a_follow_up_reaches_the_model_as_a_valid_conversation(tmp_path):
    """Providers refuse a tool call with no result: the finished job's last call
    must be answered, and the new job must arrive in the same turn."""
    from rimuru.llm.base import LLMResponse, ToolCall

    async def run():
        system = make_system(tmp_path)
        thread, _task, first, worker = await clone_for(system)
        finished_conversation(first.id, system)
        await system.pool._on_finish(worker, "done", "Built it.", "Ready.")
        await dispatch(system, thread)
        follow = next(t for t in system.store.tasks.values() if t.title == "Make it blue")
        again = system.pool.workers[follow.worker_id]
        seen = []

        async def complete(*, system: str, messages, tools, max_tokens=2048, role="worker"):
            seen.append([(m.role, m.content) for m in messages])
            return LLMResponse(text="", tool_calls=[ToolCall(id="c2", name="finish", args={
                "summary": "Made it blue.", "outcome": "done", "for_person": "It is blue now."})])

        again.loop.client.complete = complete
        again.start()
        end = time.time() + 10
        while time.time() < end and follow.status != "done":
            await asyncio.sleep(0.02)
        assert follow.status == "done" and follow.result == "Made it blue."
        roles = [r for r, _c in seen[0]]
        assert roles == ["user", "assistant", "user"], roles
        blocks = seen[0][2][1]
        assert blocks[0]["type"] == "tool_result" and blocks[0]["tool_use_id"] == "call_finish"
        assert any("A FOLLOW-UP, FOR YOU" in str(b) for b in blocks[1:]), "the new job is in the same turn"
        assert system.store.chat[-1]["text"] == "It is blue now." and system.store.chat[-1]["clone"] == first.name
        await system.stop()

    asyncio.run(run())


def test_a_ready_made_setup_in_one_click(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.llm import catalog
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "data" / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "data" / "sessions", "models_path": tmp_path / "models.toml",
                        "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    preset = catalog.PRESETS["free"]

    async def offered(_provider, **_kw):
        return "https://x.test", [preset["router"], preset["worker"], "other"]

    monkeypatch.setattr(catalog, "list_models", offered)
    with TestClient(create_app(speed=50)) as client:
        state = client.get("/api/models").json()
        assert [p["id"] for p in state["presets"]] == ["free"]
        done = client.post("/api/models/preset", json={"id": "free"}).json()
        assert done["roles"]["router"] == "preset-rimuru" and done["roles"]["worker"] == "preset-clones"
        assert done["active"]["router"]["model"] == preset["router"] and done["active"]["worker"]["model"] == preset["worker"]
        assert "preset-rimuru" in (tmp_path / "models.toml").read_text()
        assert client.post("/api/models/preset", json={"id": "nope"}).status_code == 400

        async def gone(_provider, **_kw):
            return "https://x.test", ["other"]

        monkeypatch.setattr(catalog, "list_models", gone)
        refused = client.post("/api/models/preset", json={"id": "free"})
        assert refused.status_code == 400 and "no longer offered" in refused.json()["error"]


# ---------------------------------------------------------------------- a clone that forgets to finish
def scripted(worker, answers):
    """Make the clone's model answer with these, in order."""
    from rimuru.llm.base import LLMResponse, ToolCall

    queue = list(answers)

    async def complete(*, system: str, messages, tools, max_tokens=2048, role="worker"):
        answer = queue.pop(0) if queue else ("still here", None)
        text, call = answer
        return LLMResponse(text=text, tool_calls=[ToolCall(id=new_id("step"), name=call[0], args=call[1])] if call else [])

    worker.loop.client.complete = complete


async def until_status(task, wanted="done", seconds=10.0):
    end = time.time() + seconds
    while time.time() < end and task.status != wanted:
        await asyncio.sleep(0.02)
    return task.status == wanted


def test_a_clone_that_forgets_to_finish_is_reminded(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _thread, task, session, worker = await clone_for(system)
        scripted(worker, [("All done, I wrote the page.", None),
                          ("", ("finish", {"summary": "Wrote it.", "outcome": "done", "for_person": "Your page is ready."}))])
        worker.start()
        assert await until_status(task)
        first, second = system.store.transcript(session.id)
        assert "without calling a tool" in second.injected and "with for_person" in second.injected
        assert system.store.chat[-1]["text"] == "Your page is ready."
        await system.stop()

    asyncio.run(run())


def test_told_twice_its_own_words_are_the_report(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _thread, task, _session, worker = await clone_for(system)
        scripted(worker, [("Working on it.", None), ("Still thinking.", None), ("It is done: open index.html.", None)])
        worker.start()
        assert await until_status(task), "the job must not hang for ever"
        assert task.result == "It is done: open index.html."
        assert system.store.chat[-1]["text"] == "It is done: open index.html."
        await system.stop()

    asyncio.run(run())


def test_a_clone_waiting_for_a_command_or_an_answer_is_left_alone(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _thread, task, session, worker = await clone_for(system)
        scripted(worker, [("", ("report", {"status": "needs_decision", "text": "Red or blue?"})), ("Waiting for the answer.", None)])
        worker.start()
        end = time.time() + 10
        while time.time() < end and session.step_count < 2:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        assert session.step_count == 2 and task.status != "done" and worker.loop.inbox.qsize() == 0, "no reminder"
        worker.loop.request_stop(status="stopped")

        _thread2, task2, session2, worker2 = await clone_for(system, key="s-foll0009")
        session2.pending_jobs.append("job_x")
        scripted(worker2, [("Waiting for the tests.", None)])
        worker2.start()
        end = time.time() + 10
        while time.time() < end and session2.step_count < 1:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        assert session2.step_count == 1 and task2.status != "done", "a running command will wake it"
        worker2.loop.request_stop(status="stopped")
        await system.stop()

    asyncio.run(run())
