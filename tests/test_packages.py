"""Clones get packages beyond the toolbox: installed in a container that has the
internet but sees only the packages folder - never the person's files - with no
package code running while installing. Offline: no Docker needed here."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.llm.mock import MockClient
from rimuru.models import Session, Task
from rimuru.system import RimuruSystem
from rimuru.worker.session import WorkerAgent
from rimuru.worker.shell import DockerShell, SimulatedShell, check_packages


def test_only_plain_package_names_are_installed():
    for names, kind in [(["requests", "flask==3.1.0", "pandas[excel]>=2.2"], "python"),
                        (["express", "lodash@4", "@types/node"], "node")]:
        assert check_packages(names, kind) is None, names
    for names, kind in [(["--index-url", "https://evil.test"], "python"),
                        (["git+https://github.com/x/y"], "python"),
                        (["../../etc/passwd"], "python"),
                        (["https://evil.test/pkg.whl"], "python"),
                        (["file:../x"], "node"), (["http://x"], "node"),
                        ([f"p{i}" for i in range(11)], "python"),
                        (["rails"], "ruby"), ([], "python")]:
        assert check_packages(names, kind), names


def test_the_installer_sees_only_the_packages_and_runs_no_package_code(tmp_path):
    shell = DockerShell(tmp_path / "ws")
    deps = tmp_path / "deps"
    py = shell._install_args(["requests"], "python", "n1", deps)
    node = shell._install_args(["express"], "node", "n2", deps)
    for args in (py, node):
        mounts = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
        assert mounts == [f"{deps.resolve()}:/deps"], "the installer must see nothing else"
        assert args[args.index("--network") + 1] == "bridge"
        assert "--read-only" in args and "ALL" in args
    assert "--only-binary=:all:" in py           # wheels: no setup.py runs
    assert "--ignore-scripts" in node            # no install scripts run


def test_work_containers_stay_offline_and_get_the_packages_read_only(tmp_path):
    shell = DockerShell(tmp_path / "ws")
    work, deps = tmp_path / "work", tmp_path / "deps"
    (deps / "python").mkdir(parents=True)
    (deps / "node" / "node_modules").mkdir(parents=True)
    work.mkdir()
    args = shell._args("node app.js", "n", work, deps)
    assert args[args.index("--network") + 1] == "none"
    assert f"{deps.resolve()}:/deps:ro" in args and "PYTHONPATH=/deps/python" in args
    assert f"{(deps / 'node' / 'node_modules').resolve()}:/workspace/node_modules:ro" in args
    # A project's own node_modules is never covered up.
    (work / "node_modules").mkdir()
    (work / "node_modules" / "left-pad").mkdir()
    assert not any(a.endswith(":/workspace/node_modules:ro") for a in shell._args("x", "n", work, deps))


def test_a_clone_installs_through_the_sandbox_and_the_person_sees_it(tmp_path):
    async def run():
        config = Config()
        config.provider = "mock"
        config.db_path, config.workspace = tmp_path / "data" / "t.db", tmp_path / "ws"
        config.sessions_dir = tmp_path / "sessions"
        system = RimuruSystem(config, speed=50.0)
        store = system.store
        asked = []

        class Sandbox:
            kind, ready = "docker", True

            async def install(self, packages, kind, deps):
                asked.append((packages, kind, Path(deps)))
                return "done", "Successfully installed requests-2.32.3"

            async def run(self, *a, **k):
                return "done", ""

        thread = store.get_or_create_thread("console", "s-pkg001", "Weather page")
        task = store.put_task(Task(id=new_id("task"), title="Weather page", brief="", thread_id=thread.id))
        session = store.put_session(Session(id=new_id("worker"), kind="worker", title=task.title,
                                            model="mock", task_id=task.id))

        async def noop(*_a, **_k):
            return True

        clone = WorkerAgent(task=task, session=session, store=store, bus=system.bus,
                            config=config, client=MockClient(), on_report=noop, on_finish=noop,
                            on_handback=noop, shell=Sandbox(), root=tmp_path / "work")
        assert "install" in clone.registry.tools
        said = await clone._tool_install({"packages": "requests --index-url=x", "kind": "python"}, clone.loop)
        assert said.startswith("Not installed") and not asked
        said = await clone._tool_install({"packages": "requests", "kind": "python"}, clone.loop)
        assert said.startswith("Installed (python): requests") and "requirements.txt" in said
        assert asked == [(["requests"], "python", clone.deps)]
        assert clone.deps.parent == (tmp_path / "data" / "deps").resolve(), "kept outside the work folder"
        lines = [m["text"] for m in store.chat if m.get("chat") == "s-pkg001" and m["role"] == "install"]
        assert lines == ["requests (Python)"]
        store.close()

    asyncio.run(run())


def test_without_docker_a_real_model_does_not_wait_for_pretend_commands():
    shell = SimulatedShell(mock=False, speed=1.0)
    started = time.perf_counter()
    status, out = asyncio.run(shell.run("pytest -q"))
    assert time.perf_counter() - started < 0.5, "found in the user test: 8-30 s per command"
    assert "did NOT run" in out
