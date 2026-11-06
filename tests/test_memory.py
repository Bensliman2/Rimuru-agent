"""Long-term memory: kept by Rimuru on its own, read back when it may matter,
passed to clones, visible to the person, never holding secrets. Before, the
Router's notes were written and never read again."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.llm.base import LLMResponse, ToolCall
from rimuru.llm.mock import MockClient
from rimuru.models import Memory, Task
from rimuru.store import Store
from rimuru.system import RimuruSystem


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "sessions"
    return RimuruSystem(config, speed=50.0)


async def until(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        await asyncio.sleep(0.02)
    return cond()


def test_rimuru_remembers_on_its_own_uses_it_later_and_never_keeps_secrets(tmp_path):
    async def run():
        system = make_system(tmp_path)
        store = system.store
        seen: list[str] = []

        class Router(MockClient):
            async def complete(self, *, messages, **kwargs):
                last = messages[-1].as_text()
                seen.append(last)
                if "Remembered [" in last or "Not remembered" in last:
                    return LLMResponse(text="ok")
                if "I love purple" in last:
                    return LLMResponse(tool_calls=[
                        ToolCall(id="r1", name="remember", args={"text": "They love purple", "about": "you"}),
                        ToolCall(id="r2", name="remember", args={"text": "My key is sk-abcdefghijklmnopqrstuvwx",
                                                                 "about": "you"})])
                return LLMResponse(text="ok")

        system.router.loop.client = Router()
        await system.start()
        await system.human_message("I love purple, by the way", thread_key="s-mem001")
        assert await until(lambda: len(store.memories) == 1)
        [kept] = store.memories.values()
        assert kept.text == "They love purple" and kept.about == "you" and kept.source == "rimuru"
        assert not any("sk-" in m.text for m in store.memories.values()), "a secret was kept"

        lines = [m for m in store.chat if m.get("chat") == "s-mem001" and m["role"] == "memory"]
        assert [m["text"] for m in lines] == ["They love purple"], "the person sees it being kept"

        await system.stop()

        # Another day, another session: it comes back to Rimuru by itself - once.
        later = make_system(tmp_path)
        later.router.loop.client = Router()
        await later.start()
        await later.human_message("Make me a small website", thread_key="s-mem002")
        assert await until(lambda: any("Make me a small website" in s for s in seen))
        turn = next(s for s in seen if "Make me a small website" in s)
        assert "What you remember" in turn and "They love purple" in turn
        await later.human_message("And a contact page", thread_key="s-mem002")
        assert await until(lambda: any("And a contact page" in s for s in seen))
        assert "They love purple" not in next(s for s in seen if "And a contact page" in s)
        await later.stop()

    asyncio.run(run())


def test_clones_get_the_persons_preferences_and_their_sessions_memories(tmp_path):
    async def run():
        system = make_system(tmp_path)
        store = system.store
        thread = store.get_or_create_thread("console", "s-mem003", "Shop site")
        other = store.get_or_create_thread("console", "s-mem004", "Taxes")
        store.put_memory(Memory(id=new_id("memory"), text="They like dark designs", about="you"))
        store.put_memory(Memory(id=new_id("memory"), text="The shop is called Miso Market",
                                about="session", session_key=thread.key))
        store.put_memory(Memory(id=new_id("memory"), text="Tax year ends in April",
                                about="session", session_key=other.key))
        task = store.put_task(Task(id=new_id("task"), title="Build the shop page", brief="A page",
                                   thread_id=thread.id))
        session, _ = await system.pool.submit(task, thread_id=thread.id)
        brief = system.pool.workers[session.id].loop.inbox.get_nowait().text
        assert "They like dark designs" in brief and "Miso Market" in brief
        assert "Tax year" not in brief, "another session's memory leaked in"
        store.close()

    asyncio.run(run())


def test_the_person_sees_adds_changes_and_forgets_memories(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "sessions", "models_path": tmp_path / "m.toml",
                        "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)

    with TestClient(create_app(speed=50)) as client:
        r = client.post("/api/memories", json={"text": "I work in Python", "about": "you"})
        assert r.status_code == 200
        mid = r.json()["memory"]["id"]
        assert client.post("/api/memories", json={"text": "password is hunter2"}).status_code == 400
        groups = {g["title"]: g for g in client.get("/api/memories").json()["groups"]}
        assert [m["text"] for m in groups["About you"]["items"]] == ["I work in Python"]
        assert client.put(f"/api/memories/{mid}", json={"text": "I work in Python 3.12"}).status_code == 200
        assert client.get("/api/memories").json()["groups"][0]["items"][0]["text"] == "I work in Python 3.12"
        found = client.get("/api/search", params={"q": "python"}).json()["items"]
        assert any("Python 3.12" in i["title"] for i in found)
        assert client.delete(f"/api/memories/{mid}").status_code == 200
        assert client.delete(f"/api/memories/{mid}").status_code == 404
        assert client.get("/api/memories").json()["count"] == 0


def test_old_write_only_notes_become_memories(tmp_path):
    db = tmp_path / "t.db"
    Store(db).close()
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO notes (ts, note) VALUES (?, ?)", (1.0, "The team deploys on Fridays"))
    store = Store(db)
    assert [(m.text, m.about) for m in store.memories.values()] == [("The team deploys on Fridays", "general")]
    store.close()
    assert len(Store(db).memories) == 1, "moved once, not on every start"
