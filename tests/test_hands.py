"""Remote hands: a session working on another machine. End to end on this PC -
the real listener and the real program for the other machine, talking over a
local port: pairing, files and commands inside the shared folders only, a clone
working there, and every refusal (wrong code, a browser, a folder not shared)."""
from __future__ import annotations

import asyncio
import json
import secrets
import socket
import time

import pytest

from rimuru.config import Config
from rimuru.hands import daemon, remote
from rimuru.hands.remote import RemoteFiles, RemoteShell
from rimuru.ids import new_id
from rimuru.llm.base import LLMResponse, ToolCall
from rimuru.llm.mock import MockClient
from rimuru.models import Task
from rimuru.system import RimuruSystem
from rimuru.worker.files import Refused


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        await asyncio.sleep(0.05)
    return cond()


def test_another_machine_pairs_and_a_session_works_in_its_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("rimuru.hands.daemon.DockerShell.available", staticmethod(lambda: False))

    async def run():
        config = Config()
        config.provider = "mock"
        config.db_path, config.workspace = tmp_path / "data" / "t.db", tmp_path / "ws"
        config.sessions_dir = tmp_path / "data" / "sessions"
        system = RimuruSystem(config, speed=50.0)
        hub = system.hands
        hub.host, hub.port = "127.0.0.1", free_port()
        await hub.listen()
        assert not hub.listen_error, hub.listen_error
        url = f"http://127.0.0.1:{hub.port}"

        shared = tmp_path / "laptop"
        (shared / "site").mkdir(parents=True)
        state = tmp_path / "hands-state.json"
        # A wrong code is refused, and the program stops rather than retrying.
        assert await daemon.serve(url, code="WRONG234", roots=[str(shared)], name="laptop",
                                  allow_host=True, once=True, state_file=state) == 1
        code = hub.new_code()
        hands = asyncio.create_task(daemon.serve(url, code=code, roots=[str(shared)], name="laptop",
                                                 allow_host=True, state_file=state))
        assert await until(lambda: hub.links), "the machine did not connect"
        [machine_id] = hub.links
        assert hub.machines[machine_id]["name"] == "laptop"
        saved = json.loads(state.read_text())
        secret = next(iter(saved.values()))["secret"]
        assert secret not in (tmp_path / "data" / "hands.json").read_text(), "only a hash is kept"
        assert code not in hub.codes, "a pairing code works once"

        # Files and commands - inside the shared folder only.
        site = str(shared / "site")
        files = RemoteFiles(hub, machine_id, site)
        await files.write("index.html", "<h1>from Rimuru</h1>")
        assert (shared / "site" / "index.html").read_text() == "<h1>from Rimuru</h1>"
        assert await files.read("index.html") == "<h1>from Rimuru</h1>"
        assert await files.list(".") == ["index.html"]
        with pytest.raises(Refused):
            await files.read("../../secret.txt")
        with pytest.raises(Refused):
            await RemoteFiles(hub, machine_id, str(tmp_path)).write("x.txt", "no")
        status, out = await RemoteShell(hub, machine_id, site, docker=False).run("echo hello-laptop")
        assert status == "done" and "hello-laptop" in out
        picked = await hub.request(machine_id, "dirs", path="")
        assert [d["path"] for d in picked["dirs"]] == [str(shared.resolve())]

        # A session pointed at that folder: its clone works over there.
        thread = system.store.get_or_create_thread("console", "s-far0001", "Laptop site")
        thread.folder = remote.ref(machine_id, site)
        task = system.store.put_task(Task(id=new_id("task"), title="Add an about page", brief="",
                                          thread_id=thread.id))

        class Clone(MockClient):
            def __init__(self):
                super().__init__()
                self.n = 0

            async def complete(self, **kwargs):
                self.n += 1
                if self.n == 1:
                    return LLMResponse(tool_calls=[ToolCall(id="c1", name="write_file", args={
                        "path": "about.html", "content": "<h1>About</h1>"})])
                return LLMResponse(tool_calls=[ToolCall(id="c2", name="finish", args={
                    "summary": "Added about.html", "outcome": "done"})])

        monkeypatch.setattr("rimuru.worker.pool.build_client", lambda *_a, **_k: Clone())
        session, _ = await system.pool.submit(task, thread_id=thread.id)
        assert await until(lambda: task.status == "done")
        assert (shared / "site" / "about.html").read_text() == "<h1>About</h1>"
        assert system.pool.workers[session.id].where == f"laptop:{site}"

        # It reconnects by itself with its secret; once removed, it is refused.
        hands.cancel()
        await asyncio.gather(hands, return_exceptions=True)
        assert await until(lambda: not hub.links)
        again = asyncio.create_task(daemon.serve(url, code=None, roots=[str(shared)], name="laptop",
                                                 allow_host=True, state_file=state))
        assert await until(lambda: machine_id in hub.links), "no reconnect with the saved secret"
        await hub.remove(machine_id)
        assert await asyncio.wait_for(again, 15) == 1, "a removed machine must be refused"
        await hub.stop()
        system.store.close()

    asyncio.run(run())


def test_a_web_page_cannot_reach_the_hands_port(tmp_path):
    import websockets

    async def run():
        config = Config()
        config.provider = "mock"
        config.db_path, config.workspace = tmp_path / "t.db", tmp_path / "ws"
        system = RimuruSystem(config, speed=50.0)
        hub = system.hands
        hub.host, hub.port = "127.0.0.1", free_port()
        await hub.listen()
        code = hub.new_code()
        with pytest.raises(websockets.exceptions.WebSocketException):
            async with websockets.connect(f"ws://127.0.0.1:{hub.port}/ws/hands",
                                          origin="https://evil.example") as ws:
                await ws.send(json.dumps({"type": "hello", "code": code}))
                await ws.recv()
        assert code in hub.codes, "a page must not even use up a code"
        await hub.stop()
        system.store.close()

    asyncio.run(run())


def test_requests_to_a_machine_that_is_away_say_so(tmp_path):
    async def run():
        config = Config()
        config.provider = "mock"
        config.db_path, config.workspace = tmp_path / "t.db", tmp_path / "ws"
        system = RimuruSystem(config, speed=50.0)
        system.hands.machines["mch_gone0001"] = {"name": "old-laptop", "secret_hash": secrets.token_hex(8)}
        files = RemoteFiles(system.hands, "mch_gone0001", "/home/x")
        with pytest.raises(Exception, match="old-laptop is not connected"):
            await files.read("a.txt")
        status, out = await RemoteShell(system.hands, "mch_gone0001", "/home/x", docker=True).run("ls")
        assert status == "failed" and "not connected" in out
        system.store.close()

    asyncio.run(run())


def test_the_app_answers_only_this_computer(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "sessions", "models_path": tmp_path / "m.toml",
                        "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    monkeypatch.delenv("RIMURU_ALLOWED_HOSTS", raising=False)
    app = create_app(speed=50)
    with TestClient(app) as here:
        assert here.get("/api/chats").status_code == 200
    with TestClient(app, client=("192.168.1.50", 50000)) as elsewhere:
        r = elsewhere.get("/api/chats", headers={"host": "localhost:8800"})
        assert r.status_code == 403, "another machine reached the app"
