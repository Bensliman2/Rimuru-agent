"""The Docker sandbox: it must run code, and it must contain it.

Each test pins one safety property. Skipped automatically when Docker is not
running or the sandbox image has not been built yet.
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from rimuru.worker.shell import SANDBOX_IMAGE, DockerShell


def _sandbox_ready() -> bool:
    if not DockerShell.available():
        return False
    probe = subprocess.run(["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True)
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not _sandbox_ready(), reason="Docker sandbox not available")


@pytest.fixture
def shell(tmp_path):
    sh = DockerShell(tmp_path)
    sh._ready.set()             # image verified above
    return sh


def run(shell, command, timeout=120):
    return asyncio.run(shell.run(command, timeout=timeout))


def test_runs_python_and_pytest(shell):
    status, out = run(shell, "python -c 'print(6*7)' && pytest --version")
    assert status == "done", out
    assert "42" in out and "pytest" in out


def test_can_write_the_workspace(shell, tmp_path):
    status, out = run(shell, "echo hello > made_in_sandbox.txt")
    assert status == "done", out
    assert (tmp_path / "made_in_sandbox.txt").read_text().strip() == "hello"


def test_has_no_network(shell):
    status, out = run(shell, "python -c \"import urllib.request; "
                             "urllib.request.urlopen('http://example.com', timeout=5)\"")
    assert status == "failed", "the sandbox reached the network"


def test_system_files_are_read_only(shell):
    status, out = run(shell, "touch /etc/should_not_exist")
    assert status == "failed"
    assert "Read-only" in out or "Permission denied" in out


def test_only_the_workspace_is_visible(shell, tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    status, out = run(shell, "ls /workspace && ls /")
    assert "marker.txt" in out
    for host_path in ("Users", "Windows", "home/hamza"):
        assert host_path not in out.split()


def test_runaway_command_is_killed(shell):
    status, out = run(shell, "sleep 60", timeout=3)
    assert status == "failed" and "container killed" in out
    leftovers = subprocess.run(
        ["docker", "ps", "--filter", f"label=rimuru.workspace={shell.owner}", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert leftovers == "", f"container survived the timeout: {leftovers}"


def test_cleanup_after_a_crash_spares_other_instances(tmp_path):
    """Found while building the speed benchmark: on start, Rimuru removed every
    sandbox container on the machine - including the running jobs of another
    Rimuru (the app, while a benchmark started). Now each cleans up only its own."""
    mine = DockerShell(tmp_path / "mine")
    theirs = DockerShell(tmp_path / "theirs")
    for sh in (mine, theirs):
        sh.workspace.mkdir(parents=True)
        sh._ready.set()
    name = f"rimuru-test-{theirs.owner}"
    subprocess.run(theirs._args("sleep 60", name)[:-4] + ["-d", SANDBOX_IMAGE, "sleep", "60"],
                   capture_output=True, check=True)
    try:
        mine.cleanup_orphans()
        alive = subprocess.run(["docker", "ps", "--filter", f"name={name}", "--format", "{{.Names}}"],
                               capture_output=True, text=True).stdout.strip()
        assert alive == name, "another instance's running job was killed"
        assert theirs.cleanup_orphans() == 1
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def test_the_toolbox_has_common_packages_and_node(shell):
    status, out = run(shell, "python -c 'import requests, flask, numpy, pandas, matplotlib; print(\"ok\")'"
                             " && node -e 'console.log(6*7)' && npm --version")
    assert status == "done", out
    assert "ok" in out and "42" in out


def test_installed_packages_work_offline_and_the_installer_never_sees_the_files(shell, tmp_path):
    work, deps = tmp_path / "work", tmp_path / "deps"
    work.mkdir()
    (work / "secret.txt").write_text("the person's file")
    status, out = asyncio.run(shell.install(["six==1.16.0"], "python", deps, timeout=300))
    assert status == "done", out
    status, out = asyncio.run(shell.run("python -c 'import six; print(six.__version__)'",
                                        timeout=60, workdir=work, deps=deps))
    assert status == "done" and "1.16.0" in out, out
    # The installer had the internet but no view of the work folder.
    args = shell._install_args(["six"], "python", "x", deps)
    assert not any(str(work) in a or a.endswith(":/workspace") for a in args)


def test_files_that_hold_secrets_are_empty_inside(shell, tmp_path):
    (tmp_path / ".env").write_text("TOKEN=super-secret-value")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("private-key-material")
    (tmp_path / "app.py").write_text("print('visible')")
    status, out = run(shell, "cat .env; cat .ssh/* 2>/dev/null; python app.py; echo written > made.txt && echo ok")
    assert status == "done", out
    assert "super-secret-value" not in out and "private-key-material" not in out
    assert "visible" in out and (tmp_path / "made.txt").read_text().strip() == "written"
    assert (tmp_path / ".env").read_text() == "TOKEN=super-secret-value", "the real file is untouched"


def test_a_folder_named_in_arabic_mounts_and_works(tmp_path):
    folder = tmp_path / "موقع-لمقهى-جديد"
    folder.mkdir()
    (folder / "ملف.txt").write_text("مرحبا", encoding="utf-8")
    sh = DockerShell(folder)
    sh._ready.set()
    status, out = run(sh, "cat ملف.txt && echo تم > نتيجة.txt && ls")
    assert status == "done", out
    assert "مرحبا" in out, "the file's text comes back readable"
    assert (folder / "نتيجة.txt").read_text(encoding="utf-8").strip() == "تم"
