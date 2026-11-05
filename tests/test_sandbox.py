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
        ["docker", "ps", "--filter", "name=rimuru-", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert leftovers == "", f"container survived the timeout: {leftovers}"
