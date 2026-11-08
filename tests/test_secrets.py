"""Keys and passwords stay where they are: a clone never opens a file that holds
them, nothing with a key in it reaches an AI service or leaves the app, and a
pull request never includes one. Offline."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from rimuru import safety
from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.models import Task
from rimuru.system import RimuruSystem
from rimuru.worker.files import LocalFiles, Refused, SecretFile
from rimuru.worker.shell import DockerShell, HostShell

KEY = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    config.max_workers = 0          # the clones wait: this tests their tools, not their work
    return RimuruSystem(config, speed=50.0)


# ---------------------------------------------------------------------- which files
@pytest.mark.parametrize("name", [
    ".env", ".env.local", "config/.env.production", "prod.env", "id_rsa", "deploy/id_ed25519",
    "server.pem", "tls/app.key", ".ssh/config", ".aws/credentials", "secrets.json", ".npmrc", "token.txt",
])
def test_files_that_hold_secrets_are_recognised(name):
    assert safety.secret_file(name)
    assert safety.secret_file(name.replace("/", "\\"))


@pytest.mark.parametrize("name", [
    ".env.example", ".env.sample", "id_rsa.pub", "tokenizer.py", "keyboard.py", "secret_santa.md",
    "docs/env.md", "app.py", "environment.yml",
])
def test_ordinary_files_are_left_alone(name):
    assert not safety.secret_file(name)


# ---------------------------------------------------------------------- what gets hidden
def test_keys_are_hidden_wherever_they_appear(monkeypatch):
    monkeypatch.setenv("MY_SERVICE_API_KEY", "abc123def456ghi789")
    monkeypatch.setenv("PWD", "/home/user1/my-project-folder")      # the current folder is not a secret
    cases = {
        "use abc123def456ghi789 now": "use [hidden key] now",
        f"pushed with {KEY}": "pushed with [hidden key]",
        "here is sk-abcdefghijklmnop1234 ok": "here is [hidden key] ok",
        "password = hunter2222": "password = [hidden key]",
        "my api key is Zk29sLq0Xv": "my api key is [hidden key]",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----": "[hidden key]",
    }
    for text, clean in cases.items():
        assert safety.redact(text) == (clean, 1), text
    for ordinary in ("the token: it is in Settings", "set your password in the app",
                     "commit 3f2a1b4c5d6e7f8091a2b3c4d5e6f708192a3b4c", "in /home/user1/my-project-folder",
                     '<div [hidden]="closed">'):
        assert safety.redact(ordinary) == (ordinary, 0), ordinary


def test_a_clones_commands_on_this_machine_get_no_keys(monkeypatch):
    env = {"PATH": "/bin", "OPENAI_API_KEY": "x", "TELEGRAM_BOT_TOKEN": "y", "DB_PASSWORD": "z", "PWD": "/w"}
    assert safety.without_secrets(env) == {"PATH": "/bin", "PWD": "/w"}

    async def run(tmp_path):
        monkeypatch.setenv("SOME_SERVICE_TOKEN", "abc123def456ghi789")
        status, output = await HostShell(tmp_path).run('python -c "import os; print(os.environ.get(\'SOME_SERVICE_TOKEN\'))"')
        return status, output

    import tempfile
    status, output = asyncio.run(run(Path(tempfile.mkdtemp())))
    assert status == "done" and "None" in output and "abc123def456ghi789" not in output


# ---------------------------------------------------------------------- a clone's files
def test_files_refuse_secrets_and_never_overwrite_them(tmp_path):
    async def run():
        files = LocalFiles(tmp_path)
        (tmp_path / ".env").write_text(f"TOKEN={KEY}")
        (tmp_path / ".env.example").write_text("TOKEN=your-token-here")
        with pytest.raises(SecretFile):
            await files.read(".env")
        with pytest.raises(SecretFile):
            await files.write(".env", "gone")
        assert KEY in (tmp_path / ".env").read_text()
        assert issubclass(SecretFile, Refused)
        assert "your-token-here" in await files.read(".env.example")
        assert await files.read("config/.env") is None       # not there: nothing to refuse
        await files.write("sub/.env", "NEW=placeholder")     # a new one holds nothing of the person's
        assert (tmp_path / "sub" / ".env").read_text() == "NEW=placeholder"

    asyncio.run(run())


def test_a_clone_cannot_open_keys_or_write_the_placeholder_into_a_file(tmp_path):
    async def run():
        system = make_system(tmp_path)
        project = tmp_path / "project"
        project.mkdir()
        (project / ".env").write_text(f"TOKEN={KEY}")
        (project / "settings.py").write_text(f'API_TOKEN = "{KEY}"\nDEBUG = True\n')
        (project / "app.py").write_text("print('hi')\n")
        thread = system.store.get_or_create_thread("console", "s-secr0001", "A project")
        thread.folder = str(project.resolve())
        task = system.store.put_task(Task(id=new_id("task"), title="Fix it", brief="", thread_id=thread.id))
        session, _ = await system.pool.submit(task, thread_id=thread.id)
        worker = system.pool.workers[session.id]

        said = await worker.loop._invoke("read_file", {"path": ".env"})
        assert said.startswith("Refused: .env holds secrets") and KEY not in said
        listed = await worker.loop._invoke("list_files", {})
        assert ".env  (holds secrets - cannot be opened)" in listed and "app.py" in listed

        # A key inside an ordinary file never reaches the AI service...
        read = await worker.loop._invoke("read_file", {"path": "settings.py"})
        assert KEY not in read and "[hidden key]" in read and "DEBUG = True" in read
        version = read.split("version: ", 1)[1].split()[0]
        # ...and the stand-in can't be written back over the real key.
        rewrite = await worker.loop._invoke("write_file", {
            "path": "settings.py", "base_version": version,
            "content": 'API_TOKEN = "[hidden key]"\nDEBUG = False\n'})
        assert rewrite.startswith("Refused, nothing was written") and KEY in (project / "settings.py").read_text()
        edit = await worker.loop._invoke("edit_file", {"path": "settings.py", "old": "[hidden key]", "new": "x"})
        assert edit.startswith("Refused, nothing was written")
        fine = await worker.loop._invoke("edit_file", {"path": "settings.py", "old": "DEBUG = True", "new": "DEBUG = False"})
        assert fine.startswith("Edited") and KEY in (project / "settings.py").read_text()
        await system.stop()

    asyncio.run(run())


def test_commands_in_the_sandbox_do_not_see_secret_files(tmp_path):
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (work / ".env").write_text("A=1")
    (work / "sub" / ".env.local").write_text("B=2")
    (work / ".ssh").mkdir()
    (work / ".ssh" / "id_rsa").write_text("k")
    (work / "node_modules").mkdir()
    (work / "node_modules" / ".env").write_text("not looked at")
    (work / "app.py").write_text("")
    shell = DockerShell(work, blank_dir=tmp_path / "data")
    args = shell._args("cat .env", "rimuru-test", work)
    blank = (tmp_path / "data" / ".rimuru-blank").resolve()
    assert blank.is_file() and blank.stat().st_size == 0
    assert f"{blank}:/workspace/.env:ro" in args and f"{blank}:/workspace/sub/.env.local:ro" in args
    assert args[args.index("/workspace/.ssh") - 1] == "--tmpfs"
    assert not any("node_modules" in a for a in args)


# ---------------------------------------------------------------------- what leaves the app
def test_a_reply_never_carries_a_key(tmp_path):
    async def run():
        system = make_system(tmp_path)
        sent, _note = await system.channels.send("console", "s-reply001", f"Done - I used {KEY} to log in.")
        assert KEY not in sent and "[hidden key]" in sent
        assert KEY not in system.store.chat[-1]["text"]
        system.router.loop.inject(f"Task finished: the key was {KEY}")
        assert KEY not in system.router.loop.inbox.get_nowait().text
        await system.stop()

    asyncio.run(run())


def test_a_key_pasted_in_the_chat_is_not_stored_or_sent(tmp_path):
    async def run():
        system = make_system(tmp_path)
        done = await system.person_says(f"please use this token {KEY} for the deploy", thread_key="s-past0001")
        trigger = system.store.triggers[done["trigger_id"]]
        assert KEY not in trigger.content and "[hidden key]" in trigger.content
        lines = [m for m in system.store.chat if m.get("chat") == "s-past0001"]
        assert KEY not in lines[0]["text"] and lines[1]["notice"] == "safety" and "Models or" in lines[1]["text"]
        assert done["said"] == lines[1]["text"], "Telegram gets the warning too"
        await system.stop()

    asyncio.run(run())


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_a_pull_request_never_includes_a_key(tmp_path):
    import httpx

    from rimuru.channels.github_live import GitHubChannel
    from rimuru.connections import Settings

    async def run():
        system = make_system(tmp_path)
        bare = tmp_path / "remote" / "owner" / "repo.git"
        bare.mkdir(parents=True)
        _git("init", "--quiet", "--bare", "-b", "main", str(bare))
        seed = tmp_path / "seed"
        _git("clone", "--quiet", str(bare), str(seed))
        (seed / "README.md").write_text("hello")
        _git("add", "-A", cwd=seed)
        _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "first", cwd=seed)
        _git("push", "--quiet", "origin", "HEAD:main", cwd=seed)

        bodies = []

        def api(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/pulls"):
                bodies.append(request.content.decode())
                return httpx.Response(201, json={"html_url": "https://github.test/pr/1", "draft": True})
            if request.url.path.endswith("/pulls"):
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"default_branch": "main"})

        settings = Settings(tmp_path / "data" / "connections.json")
        github = GitHubChannel(token="t", settings=settings, system=system,
                               http=httpx.AsyncClient(transport=httpx.MockTransport(api)))
        github.remote = str(tmp_path / "remote" / "{repo}.git")
        thread = system.store.get_or_create_thread("gh", "owner/repo#7", "An issue")
        task = system.store.put_task(Task(id=new_id("task"), title="Add the feature", brief="", thread_id=thread.id))
        folder = await github.prepare(thread, task)
        (folder / "feature.py").write_text("print('new')\n")
        (folder / ".env").write_text(f"TOKEN={KEY}\n")
        (folder / "config.py").write_text(f'TOKEN = "{KEY}"\n')

        class Worker:
            pass

        worker = Worker()
        worker.task = task
        said = await github.publish(worker, thread, "done", f"Added it. Used {KEY} to test.")
        assert "pull request: https://github.test/pr/1" in said
        assert "left out because they hold keys" in said and ".env" in said and "config.py" in said
        assert bodies and KEY not in bodies[0]

        pushed = _git("ls-tree", "-r", "--name-only", "refs/heads/" + github.branch(7), cwd=bare).split()
        assert "feature.py" in pushed and ".env" not in pushed and "config.py" not in pushed
        log = _git("log", "-1", "--format=%B", "refs/heads/" + github.branch(7), cwd=bare)
        assert KEY not in log
        await github.http.aclose()
        await system.stop()

    asyncio.run(run())
