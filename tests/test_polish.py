"""Clones with names and personas; deleting a session for good; forgetting a
memory from the chat. Offline: the demo model, or scripted ones."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from rimuru import persona
from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.llm.base import Msg
from rimuru.models import Memory, Task
from rimuru.system import RimuruSystem


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    return RimuruSystem(config, speed=50.0)


async def until(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        await asyncio.sleep(0.02)
    return cond()


def test_clones_have_names_and_personas_that_stay_theirs(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread = system.store.get_or_create_thread("console", "s-name001", "Two jobs")
        sessions = []
        for title in ("Build a page", "Write a script"):
            task = system.store.put_task(Task(id=new_id("task"), title=title, brief="", thread_id=thread.id))
            session, _ = await system.pool.submit(task, thread_id=thread.id)
            sessions.append(session)
        a, b = sessions
        assert a.name in persona.NAMES and b.name in persona.NAMES
        assert a.name != b.name, "two clones at work never share a name"
        assert a.persona in persona.PERSONAS
        brief = system.pool.workers[a.id].loop.inbox.get_nowait().text
        assert brief.startswith(f"You are {a.name}, one of Rimuru's clones.")
        card = persona.card(a)
        assert card["name"] == a.name and card["persona"] == persona.PERSONAS[a.persona][0]
        await system.stop()
        return a.id, a.name

    clone_id, name = asyncio.run(run())

    async def again():
        system = make_system(tmp_path)
        kept = system.store.sessions[clone_id]
        system.store.close()
        return kept.name

    assert asyncio.run(again()) == name, "a clone keeps its name after a restart"


def test_deleting_a_session_forgets_it_everywhere_and_files_only_when_asked(tmp_path, steady_demo):
    async def run():
        system = make_system(tmp_path)
        store = system.store
        await system.start()
        await system.human_message("Build a small page about cats", thread_key="s-del00001")
        await system.human_message("Write a haiku about dogs", thread_key="s-keep0001")
        assert await until(lambda: [t for t in store.tasks.values() if t.status == "done"
                                    and store.threads[t.thread_id].key == "s-del00001"], timeout=20)
        thread = next(t for t in store.threads.values() if t.key == "s-del00001")
        folder = Path(next(t.folder for t in store.tasks.values() if t.thread_id == thread.id))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cats.html").write_text("<h1>cats</h1>")
        store.put_memory(Memory(id=new_id("memory"), text="The cat is called Miso", about="session",
                                session_key="s-del00001"))
        store.put_memory(Memory(id=new_id("memory"), text="They like short answers", about="you"))
        clones = [t.worker_id for t in store.tasks.values() if t.thread_id == thread.id]
        assert any(thread.id in m.as_text() or "cats" in m.as_text() for m in system.router.loop.messages)

        result = await system.delete_session("s-del00001")
        assert result["folder_deleted"] is None and folder.is_dir(), "files stay unless asked"
        assert "console:s-del00001" not in store.thread_by_key
        assert not [m for m in store.chat if m.get("chat") == "s-del00001"]
        assert not [t for t in store.tasks.values() if t.thread_id == thread.id]
        assert not [c for c in clones if c in store.sessions or store.steps.get(c)]
        assert [m.text for m in store.memories.values()] == ["They like short answers"]
        said = "\n".join(m.as_text() for m in system.router.loop.messages)
        assert "cats" not in said and thread.id not in said, "Rimuru still had it in mind"
        turns = json.dumps([s.to_dict() for s in store.transcript(system.router.session.id)])
        assert "cats" not in turns and thread.id not in turns, "Rimuru's turn log still shows it"
        assert any(m.get("chat") == "s-keep0001" for m in store.chat), "another session was touched"
        await system.stop()

        # After a restart nothing of it comes back - not even in Rimuru's saved mind.
        again = make_system(tmp_path)
        await again.start()
        assert "console:s-del00001" not in again.store.thread_by_key
        assert "cats" not in json.dumps(again.store.load_context(again.router.session.id) or {})
        await again.stop()
        return folder

    asyncio.run(run())


def test_only_rimurus_own_folder_is_deleted_and_only_when_asked(tmp_path):
    async def run():
        system = make_system(tmp_path)
        store = system.store
        mine = system.config.sessions_dir / "a-page"
        mine.mkdir(parents=True)
        (mine / "index.html").write_text("x")
        made = store.get_or_create_thread("console", "s-made0001", "A page")
        made.rimuru_folder = str(mine.resolve())
        store.add_chat("user", "make a page", chat="s-made0001")
        theirs = tmp_path / "my-project"
        theirs.mkdir()
        (theirs / "app.py").write_text("print(1)")
        picked = store.get_or_create_thread("console", "s-pick0001", "My project")
        picked.folder = str(theirs.resolve())
        store.add_chat("user", "fix my project", chat="s-pick0001")

        r1 = await system.delete_session("s-made0001", delete_folder=True)
        r2 = await system.delete_session("s-pick0001", delete_folder=True)
        system.store.close()
        return r1, r2, mine, theirs

    r1, r2, mine, theirs = asyncio.run(run())
    assert r1["folder_deleted"] and not mine.exists()
    assert r2["folder_deleted"] is None and (theirs / "app.py").exists(), "a picked folder is never deleted"


def test_the_app_deletes_sessions_and_forgets_memories_from_the_chat(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "data" / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "data" / "sessions", "models_path": tmp_path / "m.toml",
                        "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    with TestClient(create_app(speed=50)) as client:
        store = client.app.state.system.store
        store.get_or_create_thread("console", "s-mem00001", "Prefs")
        mem = store.put_memory(Memory(id=new_id("memory"), text="They love purple", about="you"))
        store.add_chat("memory", mem.text, chat="s-mem00001", memory_id=mem.id)
        line = client.get("/api/chat", params={"chat": "s-mem00001"}).json()["messages"][-1]
        assert line["role"] == "memory" and line["kept"] is True
        assert client.delete(f"/api/memories/{mem.id}", params={"chat": "s-mem00001"}).status_code == 200
        lines = client.get("/api/chat", params={"chat": "s-mem00001"}).json()["messages"]
        assert lines[-2]["kept"] is False and lines[-1]["forgot"] is True and lines[-1]["text"] == "They love purple"

        assert client.delete("/api/chats/s-mem00001").json()["ok"] is True
        assert client.delete("/api/chats/s-mem00001").status_code == 404
        assert all(c["key"] != "s-mem00001" for c in client.get("/api/chats").json()["items"])
