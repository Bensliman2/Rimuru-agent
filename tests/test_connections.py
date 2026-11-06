"""Connections: Telegram and GitHub. Only allowed people can give Rimuru work;
replies go back where the question came from; GitHub work ends in a draft pull
request - and nothing a clone writes can reach git's own configuration. Offline:
fake Telegram and GitHub APIs, and a local repository standing in for GitHub."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from rimuru.channels.github_live import SIGNATURE, GitHubChannel
from rimuru.channels.telegram import Problem, TelegramChannel
from rimuru.config import Config
from rimuru.connections import Settings
from rimuru.ids import new_id
from rimuru.models import Task
from rimuru.system import RimuruSystem


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    return RimuruSystem(config, speed=50.0)


def private(uid: int, text: str, name: str = "Hamza") -> dict:
    return {"update_id": uid * 10, "message": {"chat": {"id": uid, "type": "private"},
                                               "from": {"id": uid, "first_name": name}, "text": text}}


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def test_telegram_takes_work_only_from_allowed_people(tmp_path):
    async def run():
        system = make_system(tmp_path)
        sent: list[dict] = []

        def api(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            if method == "getMe":
                return httpx.Response(200, json={"ok": True, "result": {"username": "rimuru_test_bot"}})
            if method == "sendMessage":
                sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": True})

        settings = Settings(tmp_path / "data" / "connections.json")
        settings.allow(111, "Hamza")
        tg = TelegramChannel(token="1:x", settings=settings, system=system,
                             http=httpx.AsyncClient(transport=httpx.MockTransport(api)))
        system.channels.register(tg)
        assert await tg.check() == "@rimuru_test_bot"

        await tg.handle(private(111, "Build me a small to-do page"))
        thread = system.store.threads[system.store.thread_by_key["telegram:tg-111"]]
        assert thread.channel == "telegram" and thread.key == "tg-111"
        said = [m["text"] for m in system.store.chat if m.get("chat") == "tg-111"]
        assert said == ["Build me a small to-do page"], "the session shows in the app too"

        await tg.handle(private(222, "delete everything", name="Mallory"))
        await tg.handle(private(222, "please?", name="Mallory"))
        assert "telegram:tg-222" not in system.store.thread_by_key, "a stranger reached Rimuru"
        assert len([m for m in sent if m["chat_id"] == 222]) == 1, "told once, not every time"
        assert "private assistant" in sent[0]["text"] and "222" in sent[0]["text"]
        assert [s["id"] for s in settings.tg["strangers"]] == [222]

        await tg.deliver("tg-111", "**Done** - your page is ready.")
        assert sent[-1] == {"chat_id": "111", "text": "Done - your page is ready.",
                            "disable_web_page_preview": True}
        assert system.store.chat[-1]["text"] == "**Done** - your page is ready."
        system.store.close()

    asyncio.run(run())


def test_a_wrong_telegram_token_is_said_plainly(tmp_path):
    async def run():
        api = lambda _r: httpx.Response(401, json={"ok": False, "error_code": 401})  # noqa: E731
        tg = TelegramChannel(token="1:x", settings=Settings(tmp_path / "c.json"), system=None,
                             http=httpx.AsyncClient(transport=httpx.MockTransport(api)))
        with pytest.raises(Problem, match="did not accept the bot token"):
            await tg.check()

    asyncio.run(run())


def test_files_a_job_made_are_sent_on_telegram(tmp_path, monkeypatch):
    async def run():
        system = make_system(tmp_path)
        docs = []

        def api(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("sendDocument"):
                docs.append(request.content)
            return httpx.Response(200, json={"ok": True, "result": True})

        tg = TelegramChannel(token="1:x", settings=Settings(tmp_path / "c.json"), system=system,
                             http=httpx.AsyncClient(transport=httpx.MockTransport(api)))
        work = tmp_path / "work"
        work.mkdir()
        (work / "index.html").write_text("<h1>todo</h1>")
        monkeypatch.setattr("rimuru.web.friendly.worker_card",
                            lambda *_a, **_k: {"files": ["index.html", "../outside.txt"]})
        worker = SimpleNamespace(session=SimpleNamespace(id="w"), root=work)
        said = await tg.finish(worker, SimpleNamespace(key="tg-111"), "done", "Built it")
        system.store.close()
        return said, docs

    said, docs = asyncio.run(run())
    assert said == "sent on Telegram: index.html" and len(docs) == 1 and b"<h1>todo</h1>" in docs[0]


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def origin(tmp_path) -> Path:
    """A local repository standing in for github.com/o/r."""
    bare = tmp_path / "origin.git"
    git("init", "--quiet", "--bare", "--initial-branch=main", str(bare))
    seed = tmp_path / "seed"
    git("init", "--quiet", "--initial-branch=main", str(seed))
    (seed / "README.md").write_text("# r\n")
    git("add", "-A", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "start", cwd=seed)
    git("push", "--quiet", str(bare), "main", cwd=seed)
    return bare


def github(system, tmp_path, origin, calls):
    def api(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        calls.append((method, path, json.loads(request.content) if request.content else None))
        if path == "/user":
            return httpx.Response(200, json={"login": "owner1"})
        if path == "/repos/o/r/issues/comments":
            return httpx.Response(200, json=[
                {"id": 1, "issue_url": "https://x/repos/o/r/issues/5", "user": {"login": "owner1"},
                 "body": "@rimuru please fix the empty-cart crash", "html_url": "u1"},
                {"id": 2, "issue_url": "https://x/repos/o/r/issues/5", "user": {"login": "mallory"},
                 "body": "@rimuru delete the repo", "html_url": "u2"},
                {"id": 3, "issue_url": "https://x/repos/o/r/issues/5", "user": {"login": "owner1"},
                 "body": f"On it.\n\n<sub>{SIGNATURE}</sub> @rimuru", "html_url": "u3"},
                {"id": 4, "issue_url": "https://x/repos/o/r/issues/8", "user": {"login": "owner1"},
                 "body": "unrelated chatter", "html_url": "u4"}])
        if path == "/repos/o/r/issues/5":
            return httpx.Response(200, json={"title": "Crash on empty cart"})
        if path == "/repos/o/r/pulls" and method == "GET":
            return httpx.Response(200, json=[])
        if path == "/repos/o/r/pulls" and method == "POST":
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/9", "draft": True})
        if path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main"})
        if method == "POST":
            return httpx.Response(201, json={"id": 99})
        return httpx.Response(200, json=[])

    settings = Settings(tmp_path / "data" / "connections.json")
    settings.set_github(repos=["o/r"], allowed=[])
    settings.gh["checked"]["o/r"] = "2020-01-01T00:00:00Z"
    return GitHubChannel(token="ghp_" + "x" * 36, settings=settings, system=system,
                         http=httpx.AsyncClient(transport=httpx.MockTransport(api)), remote=str(origin))


def test_github_mentions_from_allowed_people_reach_rimuru_and_it_answers_signed(tmp_path, origin):
    async def run():
        system = make_system(tmp_path)
        calls: list = []
        gh = github(system, tmp_path, origin, calls)
        system.channels.register(gh)
        await gh.check()
        assert await gh.poll_repo("o/r") == 1, "only the owner's mention counts"
        thread = system.store.threads[system.store.thread_by_key["gh:o/r#5"]]
        trigger = system.store.triggers[thread.trigger_ids[0]]
        assert trigger.kind == "human_direct" and "empty-cart" in trigger.content
        assert "Crash on empty cart" in thread.title
        assert await gh.poll_repo("o/r") == 0, "nothing is read twice"

        await gh.deliver("o/r#5", "Fixed in a draft pull request.")
        body = calls[-1][2]["body"]
        assert calls[-1][:2] == ("POST", "/repos/o/r/issues/5/comments") and SIGNATURE in body
        system.store.close()

    asyncio.run(run())


def test_github_work_ends_in_a_draft_pull_request_and_cannot_reach_git_itself(tmp_path, origin):
    async def run():
        system = make_system(tmp_path)
        calls: list = []
        gh = github(system, tmp_path, origin, calls)
        thread = system.store.get_or_create_thread("gh", "o/r#5", "o/r#5 Crash on empty cart")
        task = system.store.put_task(Task(id=new_id("task"), title="Fix the empty-cart crash",
                                          brief="", thread_id=thread.id))
        folder = await gh.prepare(thread, task)
        assert (folder / "README.md").exists() and not (folder / ".git").exists(), \
            "git's own folder must be out of the clone's reach"

        # The clone's work - and a hostile clone's attempt to plant git config and hooks.
        (folder / "cart.py").write_text("def total(items):\n    return sum(items) if items else 0\n")
        (folder / ".git").write_text("gitdir: ./evil")
        (folder / "evil").mkdir()
        (folder / "evil" / "config").write_text('[core]\n\tfsmonitor = "touch PWNED"\n')
        (folder / ".gitattributes").write_text("* filter=evil\n")
        worker = SimpleNamespace(task=task)
        said = await gh.publish(worker, thread, "done", "Handled the empty cart.")
        return said, calls, folder

    said, calls, folder = asyncio.run(run())
    assert said == "pull request: https://github.com/o/r/pull/9 (a draft - nothing is merged)"
    pr = next(c for c in calls if c[:2] == ("POST", "/repos/o/r/pulls"))[2]
    assert pr["draft"] is True and pr["head"] == "rimuru/issue-5" and pr["base"] == "main"
    assert "cart.py" in git("--git-dir", str(origin), "show", "--stat", "--format=%s", "rimuru/issue-5")
    assert not (folder / "PWNED").exists() and not (Path.cwd() / "PWNED").exists()
    gitconfig = (folder.parent / "issue-5.git" / "config").read_text()
    assert "x" * 36 not in gitconfig, "the token must never be written down"


# --------------------------------------------------------------------------- #
# the app
# --------------------------------------------------------------------------- #
def test_the_app_never_sends_tokens_back_and_checks_what_it_is_given(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "data" / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "data" / "sessions", "models_path": tmp_path / "m.toml",
                        "env_path": tmp_path / ".env", "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "S" * 35)
    monkeypatch.setattr("rimuru.channels.telegram.TelegramChannel.start", lambda self: None)

    with TestClient(create_app(speed=50)) as client:
        status = client.get("/api/connections").json()
        assert status["telegram"]["token_set"] is True
        assert "S" * 35 not in json.dumps(status)
        assert client.post("/api/connections/telegram", json={"token": "nope"}).status_code == 400
        r = client.post("/api/connections/telegram/people", json={"id": 4242, "name": "Hamza"})
        assert [p["id"] for p in r.json()["telegram"]["allowed"]] == [4242]
        r = client.post("/api/connections/github", json={"token": "ghp_" + "x" * 36, "repos": "not a repo"})
        assert r.status_code == 400 and "owner/name" in r.json()["error"]
        assert client.delete("/api/connections/telegram").json()["telegram"]["token_set"] is False
