"""After a restart, work resumes exactly where it stopped: the same clone, with its
whole conversation - not a new clone with a summary - up to three times, and it
stops sooner when a resumed job gets nowhere. Offline: scripted models."""
from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path

import pytest

from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.llm.base import LLMResponse, ToolCall
from rimuru.llm.mock import MockClient
from rimuru.models import Task
from rimuru.system import RimuruSystem


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "sessions"
    return RimuruSystem(config, speed=50.0)


async def until(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        await asyncio.sleep(0.02)
    return cond()


class Script(MockClient):
    """A clone's model: each call answers from a list; None = think forever."""

    def __init__(self, answers):
        super().__init__()
        self.answers = list(answers)
        self.seen: list[list] = []

    async def complete(self, *, messages, **kwargs):
        self.seen.append(list(messages))
        answer = self.answers.pop(0) if self.answers else LLMResponse(text="ok")
        if answer is None:
            await asyncio.sleep(3600)
        return answer


def call(name, **args):
    return LLMResponse(tool_calls=[ToolCall(id=f"c-{secrets.token_hex(4)}", name=name, args=args)])


def first_run(tmp_path, monkeypatch, answers, *, crash=True, wait=None):
    """Start a job in a chat session, let it get going, then crash (or stop)."""
    async def run():
        system = make_system(tmp_path)
        script = Script(answers)
        monkeypatch.setattr("rimuru.worker.pool.build_client", lambda *_a, **_k: script)
        await system.start()
        thread = system.store.get_or_create_thread("console", "s-res001", "Build the page")
        task = system.store.put_task(Task(id=new_id("task"), title="Build the page",
                                          brief="Make page.html", thread_id=thread.id))
        session, _ = await system.pool.submit(task, thread_id=thread.id)
        assert await until(lambda: (wait or (lambda: len(script.seen) >= 2))(system, session.id)
                           if wait else len(script.seen) >= 2)
        if crash:
            system.store.close()          # dies here: nothing more is saved
        else:
            await system.stop()           # a normal restart
        return task.id, session.id

    return asyncio.run(run())


def second_run(tmp_path, monkeypatch, answers):
    async def run():
        system = make_system(tmp_path)
        script = Script(answers)
        monkeypatch.setattr("rimuru.worker.pool.build_client", lambda *_a, **_k: script)
        await system.start()
        await until(lambda: script.seen and not system.pool.live, timeout=10)
        tasks = {t.id: t for t in system.store.tasks.values()}
        said = [m["text"] for m in system.store.chat if m["role"] == "rimuru"]
        contexts = {sid: system.store.load_context(sid) for sid in system.store.sessions}
        await system.stop()
        return script, tasks, said, contexts

    return asyncio.run(run())


def test_a_crash_resumes_the_same_clone_exactly_where_it_stopped(tmp_path, monkeypatch):
    task_id, worker = first_run(tmp_path, monkeypatch, [
        call("write_file", path="page.html", content="<h1>one</h1>"), None])
    assert (tmp_path / "sessions").exists()

    script, tasks, said, contexts = second_run(tmp_path, monkeypatch, [
        call("finish", summary="Built page.html", outcome="done")])
    task = tasks[task_id]
    assert task.worker_id == worker, "the same clone carries on - not a new one"
    assert task.attempt == 2 and task.status == "done"
    seen = script.seen[0]
    text = "\n".join(m.as_text() for m in seen)
    assert "Make page.html" in text                               # its brief, as it was
    assert any(m.role == "assistant" and any(b.get("name") == "write_file" for b in m.content
                                             if isinstance(b, dict)) for m in seen)
    assert "RESUMED AFTER A RESTART" in text and "Cut off" not in text
    assert any("exactly where I left off" in s for s in said)
    assert contexts.get(worker), "kept for a while, for a follow-up (tests/test_followups.py)"


def test_a_tool_cut_off_by_the_restart_is_marked_as_such(tmp_path, monkeypatch):
    async def hang(*_a, **_k):
        await asyncio.sleep(3600)

    monkeypatch.setattr("rimuru.worker.session.WorkerAgent._tool_list_files", hang)
    task_id, worker = first_run(
        tmp_path, monkeypatch, [call("list_files", path=".")],
        wait=lambda system, sid: system.store.sessions[sid].status == "tool")
    monkeypatch.undo()

    script, tasks, _said, _ = second_run(tmp_path, monkeypatch, [
        call("finish", summary="done", outcome="done")])
    last = script.seen[0][-1]
    assert last.role == "user" and "Cut off: the system restarted while this was running" in last.as_text()
    assert tasks[task_id].worker_id == worker


def test_a_normal_restart_resumes_running_work_instead_of_failing_it(tmp_path, monkeypatch):
    """Found while building this: stopping the app marked every running job
    "failed", so only a crash was ever resumed."""
    task_id, worker = first_run(tmp_path, monkeypatch, [
        call("write_file", path="page.html", content="<h1>one</h1>"), None], crash=False)
    _script, tasks, said, _ = second_run(tmp_path, monkeypatch, [
        call("finish", summary="Built page.html", outcome="done")])
    assert tasks[task_id].status == "done" and tasks[task_id].worker_id == worker
    assert any("exactly where I left off" in s for s in said)


@pytest.mark.parametrize("attempt, mark, why", [(2, 1, "before it could get anywhere"),
                                                (4, None, "4 times")])
def test_a_job_that_keeps_dying_is_stopped_not_looped(tmp_path, monkeypatch, attempt, mark, why):
    task_id, worker = first_run(tmp_path, monkeypatch, [
        call("write_file", path="page.html", content="x"), None])

    async def fake_history():                 # as if it had been resumed before
        system = make_system(tmp_path)
        system.store.update_task(system.store.tasks[task_id], attempt=attempt, resume_mark=mark)
        system.store.close()

    asyncio.run(fake_history())
    _script, tasks, said, _ = second_run(tmp_path, monkeypatch, [])
    assert tasks[task_id].status == "failed"
    assert any("interrupted again" in s and why in s for s in said), said


def test_rimuru_gets_its_own_conversation_back_after_a_restart(tmp_path):
    async def run():
        system = make_system(tmp_path)
        await system.start()
        await system.human_message("Please remember my cat is called Miso", thread_key="s-cat001")
        assert await until(lambda: system.store.transcript(system.router.session.id))
        await system.stop()

        again = make_system(tmp_path)
        assert not again.router.loop.messages
        await again.start()
        restored = "\n".join(m.as_text() for m in again.router.loop.messages)
        await again.stop()
        return restored

    assert "my cat is called Miso" in asyncio.run(run())
