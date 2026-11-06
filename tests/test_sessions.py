"""Sessions: separate conversations, each with its own chat, clones and folder -
and a memory you can search by words instead of IDs. Offline (demo model)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rimuru.config import Config
from rimuru.sessions import folder_for
from rimuru.system import RimuruSystem


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.demo = True
    config.db_path = tmp_path / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "sessions"
    return RimuruSystem(config, speed=50.0)


def test_each_session_keeps_its_own_chat_clones_and_folder(tmp_path):
    async def run():
        system = make_system(tmp_path)
        store = system.store
        await system.start()
        await system.human_message("Build a calculator with tests", thread_key="s-aaaa11")
        await system.human_message("Please fix the broken login page", thread_key="s-bbbb22")
        deadline = time.time() + 20
        while time.time() < deadline:
            replies = {m.get("chat") for m in store.chat if m["role"] == "rimuru"}
            if len(store.tasks) >= 2 and {"s-aaaa11", "s-bbbb22"} <= replies:
                break
            await asyncio.sleep(0.05)

        a = store.threads[store.thread_by_key["console:s-aaaa11"]]
        b = store.threads[store.thread_by_key["console:s-bbbb22"]]
        assert a.title == "Build a calculator with tests"      # named after the first message

        chat_a = " ".join(m["text"] for m in store.chat if m.get("chat") == "s-aaaa11")
        assert "calculator" in chat_a and "login" not in chat_a, "the sessions' chats got mixed"
        assert any(m["role"] == "rimuru" for m in store.chat if m.get("chat") == "s-bbbb22"), \
            "the reply went to the wrong session"

        job_a = next(t for t in store.tasks.values() if t.thread_id == a.id)
        job_b = next(t for t in store.tasks.values() if t.thread_id == b.id)
        assert job_a.id != job_b.id
        folders = {folder_for(system.config, a), folder_for(system.config, b)}
        assert len(folders) == 2 and all(f.parent == system.config.sessions_dir.resolve() for f in folders)
        clone = system.pool.workers.get(job_a.worker_id)
        if clone is not None:                                   # still running
            assert clone.root == folder_for(system.config, a)
        await system.stop()

    asyncio.run(run())


def test_the_app_lists_sessions_and_shows_each_one_separately(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "sessions", "models_path": tmp_path / "m.toml",
                        "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)

    with TestClient(create_app(speed=50)) as client:
        client.post("/api/message", json={"text": "Build a calculator with tests", "thread_key": "s-one111"})
        client.post("/api/message", json={"text": "Write a haiku about slimes", "thread_key": "s-two222"})
        deadline = time.time() + 20
        while time.time() < deadline and not client.get("/api/work", params={"chat": "s-one111"}).json()["cards"]:
            time.sleep(0.1)

        titles = [c["title"] for c in client.get("/api/chats").json()["items"]]
        assert "Build a calculator with tests" in titles and "Write a haiku about slimes" in titles

        one = client.get("/api/chat", params={"chat": "s-one111"}).json()["messages"]
        assert any("calculator" in (m.get("text") or "") for m in one)
        assert not any("haiku" in (m.get("text") or "") for m in one)

        cards = client.get("/api/work", params={"chat": "s-one111"}).json()["cards"]
        assert cards and all("haiku" not in c["title"].lower() for c in cards)
        assert not client.get("/api/work", params={"chat": "s-two222"}).json()["cards"] or \
            all("calculator" not in c["title"].lower()
                for c in client.get("/api/work", params={"chat": "s-two222"}).json()["cards"])

        # Memory: found by a word, no ID needed.
        found = client.get("/api/search", params={"q": "calculator"}).json()["items"]
        kinds = {i["kind"] for i in found}
        assert {"session", "job"} <= kinds, found
        assert all("calculator" in (i["title"] + i["sub"]).lower() or i["kind"] in {"job", "message"}
                   for i in found)
        recent = client.get("/api/search").json()["items"]
        assert recent, "with no words, recent things are listed to click"

        # Memory, session by session: each session lists its own clones and their jobs.
        memory = client.get("/api/memory").json()
        by_title = {g["title"]: g for g in memory["sessions"]}
        calc = by_title["Build a calculator with tests"]
        assert calc["clones"] and all("haiku" not in c["title"].lower() for c in calc["clones"])
        assert all(c["title"] for c in calc["clones"])


def test_work_asked_for_in_a_session_is_listed_there_even_without_its_thread(tmp_path):
    """Found in real use: Rimuru dispatched "Build a calculator with a web UI"
    without a thread_id, so its clone belonged to no session."""
    from rimuru.llm.base import Msg

    async def run():
        system = make_system(tmp_path)
        router = system.router
        session = system.store.get_or_create_thread("console", "s-cafe01", "Build a calculator")

        # The turn's own input names the session's thread: that wins.
        router.loop.messages.append(Msg("user", '[trg_1] (console s-cafe01) you: "build it"\n'
                                                f"    kind=human_direct | thread: {session.id}"))
        await router._tool_dispatch({"title": "Build a calculator", "brief": "b"}, router.loop)
        first = next(t for t in system.store.tasks.values() if t.title == "Build a calculator")
        assert first.thread_id == session.id

        # Otherwise: the session the person wrote in moments ago.
        router.loop.messages.clear()
        router.latest_console_thread, router.latest_console_ts = session.id, time.time()
        await router._tool_dispatch({"title": "Add a dark mode", "brief": "b"}, router.loop)
        second = next(t for t in system.store.tasks.values() if t.title == "Add a dark mode")
        assert second.thread_id == session.id
        await system.pool.shutdown()
        system.store.close()

    asyncio.run(run())



def test_out_of_credit_is_said_once_in_the_session_and_the_work_carries_on(tmp_path):
    """The model cannot speak when the provider refuses the account, so the app
    tells the person itself - once - keeps retrying, and says when it is back."""
    from rimuru.llm.base import LLMResponse, ProviderAccountError, ToolCall
    from rimuru.llm.mock import MockClient

    async def run():
        system = make_system(tmp_path)
        store = system.store
        calls = {"n": 0}

        class OutOfCredit(MockClient):
            async def complete(self, **kwargs):
                calls["n"] += 1
                if calls["n"] <= 3:
                    raise ProviderAccountError("credit", "Kimi (Moonshot)", "insufficient balance")
                if calls["n"] == 4:
                    return LLMResponse(tool_calls=[ToolCall(id="r1", name="reply", args={
                        "thread_id": "console", "text": "Here is your calculator plan."})])
                return LLMResponse(text="ok")

        system.router.loop.client = OutOfCredit()
        system.router.loop._blocked_wait = 0.05
        system.config.blocked_retry_seconds = 0.05
        await system.start()
        await system.human_message("Build me a calculator", thread_key="s-0001")
        deadline = time.time() + 10
        said: list[str] = []
        while time.time() < deadline:
            said = [m["text"] for m in store.chat if m.get("chat") == "s-0001" and m["role"] == "rimuru"]
            if any("calculator plan" in s for s in said):
                break
            await asyncio.sleep(0.02)

        assert sum("out of credit" in s for s in said) == 1, said   # told once, not per retry
        assert "platform.moonshot.ai" in said[0]                    # and where to top it up
        assert any("working again" in s for s in said), said
        assert said[-1] == "Here is your calculator plan."           # the work carried on by itself
        assert system.router.session.status != "failed"
        assert not system.blocked
        await system.stop()

    asyncio.run(run())



def test_every_session_has_one_clear_folder_and_clones_name_real_paths(tmp_path):
    """The first session used to share the practice folder - which a practice run
    empties - and a clone's result said "open /workspace/todo.html", a path inside
    the sandbox that means nothing on the person's computer."""
    from types import SimpleNamespace

    from rimuru.ids import new_id
    from rimuru.llm.mock import MockClient
    from rimuru.models import Session, Task
    from rimuru.sessions import folder_name
    from rimuru.worker.session import WorkerAgent

    system = make_system(tmp_path)
    first = system.store.get_or_create_thread("console", "direct", "Build a to-do page")
    folder = folder_for(system.config, first)
    assert folder.parent == system.config.sessions_dir.resolve(), "the first session must have its own folder"
    assert folder.name == "build-a-to-do-page-direct"
    assert folder_name(SimpleNamespace(title="A very long request about a calculator with a web UI "
                                             "and keyboard support", key="s-1a2b3c")) == \
        "a-very-long-request-about-a-calculator-1a2b3c"           # cut at a word, never mid-word

    async def noop(*_a, **_k):
        return True

    session = system.store.put_session(Session(id=new_id("worker"), kind="worker", title="todo", model="m"))
    clone = WorkerAgent(task=Task(id=new_id("task"), title="todo", brief="", thread_id=first.id),
                        session=session, store=system.store, bus=system.bus, config=system.config,
                        client=MockClient(), on_report=noop, on_finish=noop, on_handback=noop,
                        shell=system.shell, root=folder)
    said = clone._real_paths("Open /workspace/todo.html in a browser. Everything is in /workspace.")
    assert "/workspace" not in said
    assert f"{clone.where}/todo.html" in said and said.endswith(f"in {clone.where}.")
    system.store.close()


def test_a_session_works_in_the_folder_the_person_picks(tmp_path, monkeypatch):
    """Each session chooses where it works: a folder of the person's, picked in
    the app - never a whole drive, the home folder or Rimuru's own folder - or,
    by default, Rimuru's own folder for it, named after the first job."""
    from fastapi.testclient import TestClient

    from rimuru import folders
    from rimuru.config import CONFIG, ROOT
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "data" / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "data" / "sessions",
                        "models_path": tmp_path / "m.toml", "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    project = tmp_path / "my-site"
    project.mkdir()
    (project / "index.html").write_text("<h1>mine</h1>", encoding="utf-8")
    opened: list[Path] = []
    monkeypatch.setattr(folders, "open_in_file_manager", opened.append)
    sessions_dir = tmp_path / "data" / "sessions"
    sessions_dir.mkdir(parents=True)

    def clone_of(client, chat):
        deadline = time.time() + 20
        while time.time() < deadline:
            cards = client.get("/api/work", params={"chat": chat}).json()["cards"]
            if cards:
                return cards[0]
            time.sleep(0.05)
        raise AssertionError(f"no clone started in {chat}")

    with TestClient(create_app(speed=50)) as client:
        system = client.app.state.system
        for bad, why in [(tmp_path / "nope", "no folder"), (Path(tmp_path.anchor), "whole drive"),
                         (Path.home(), "home folder"), (ROOT, "Rimuru itself"),
                         (ROOT / "rimuru", "part of Rimuru"), (tmp_path, "Rimuru's records"),
                         (project / "index.html", "a file"), (sessions_dir, "every session")]:
            r = client.post("/api/session/folder", json={"chat": "s-pick01", "path": str(bad)})
            assert r.status_code == 400 and why in r.json()["error"], (bad, r.json())

        # Picked before anything was said; the first message still names the session.
        r = client.post("/api/session/folder", json={"chat": "s-pick01", "path": str(project)})
        assert r.status_code == 200, r.json()
        assert r.json()["folder"]["kind"] == "picked" and r.json()["folder"]["path"] == str(project.resolve())
        client.post("/api/message", json={"text": "Build a contact page for my site", "thread_key": "s-pick01"})
        card = clone_of(client, "s-pick01")
        task = system.store.tasks[system.store.sessions[card["id"]].task_id]
        assert Path(task.folder) == project.resolve()
        deadline = time.time() + 10
        while time.time() < deadline and not system.store.transcript(card["id"]):
            time.sleep(0.05)
        brief = system.store.transcript(card["id"])[0].injected
        assert str(project.resolve()) in brief and "person's own folder" in brief
        titles = {c["key"]: c["title"] for c in client.get("/api/chats").json()["items"]}
        assert titles["s-pick01"] == "Build a contact page for my site"

        # Not while its clone works; and Open shows exactly that folder.
        if client.get("/api/work", params={"chat": "s-pick01"}).json()["folder"]["working"]:
            r = client.post("/api/session/folder", json={"chat": "s-pick01", "path": ""})
            assert r.status_code == 409
        assert client.post("/api/session/open-folder", json={"chat": "s-pick01"}).status_code == 200
        assert opened == [project.resolve()]

        # The default: Rimuru's own folder, named after the first job, and kept.
        client.post("/api/message", json={"text": "Build a browser calculator with big buttons",
                                           "thread_key": "s-made01"})
        clone_of(client, "s-made01")
        info = client.get("/api/work", params={"chat": "s-made01"}).json()["folder"]
        assert info["kind"] == "made" and info["name"] == "browser-calculator-with-big-buttons", info
        made = Path(info["path"])
        assert made.parent == (tmp_path / "data" / "sessions").resolve() and made.is_dir()
        thread = next(t for t in system.store.threads.values() if t.key == "s-made01")
        thread.title = "Renamed later"
        assert client.get("/api/work", params={"chat": "s-made01"}).json()["folder"]["path"] == str(made)

        # Under the hood, every clone says which session it works for.
        sessions = {c["session"]["key"] for c in client.get("/api/work").json()["cards"] if c["session"]}
        assert {"s-pick01", "s-made01"} <= sessions

        # The folder picker lists folders by name only, and says when one cannot be used.
        listed = client.post("/api/folders", json={"path": str(tmp_path)}).json()
        assert {"my-site", "data"} <= {d["name"] for d in listed["dirs"]}
        assert not listed["usable"] and listed["why_not"]
        made_new = client.post("/api/folders/new", json={"parent": str(project), "name": "blog"})
        assert made_new.status_code == 200 and (project / "blog").is_dir()
        assert client.post("/api/folders/new", json={"parent": str(project), "name": "a/b"}).status_code == 400
