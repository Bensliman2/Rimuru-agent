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
