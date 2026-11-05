"""Where a worker's commands actually run.

Three backends:

  docker     An isolated Linux container per command: Python 3.12 + pytest, the
             workspace mounted as the working directory, no network, a read-only
             root filesystem, every capability dropped, and caps on memory, CPU
             and process count. The model's commands can touch the workspace and
             nothing else. This is the default whenever Docker is available.

  simulated  Runs nothing. Stands in for the time a command would take, and
             says so plainly in its output so a real model never mistakes it
             for a passing test run. Used with the offline mock, or without Docker.

  host       Runs directly on this machine, unsandboxed. Only by explicit opt-in.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

SANDBOX_IMAGE = "rimuru-sandbox:py3.12"
SANDBOX_DIR = Path(__file__).resolve().parents[2] / "sandbox"


def _decode(raw: bytes | None) -> str:
    return (raw or b"").decode("utf-8", errors="replace")


class SimulatedShell:
    kind = "simulated"
    ready = True

    def __init__(self, *, mock: bool, speed: float = 1.0) -> None:
        self.mock = mock
        self.speed = max(speed, 0.001)

    def describe(self) -> str:
        return "simulated - nothing actually runs"

    async def run(self, command: str, *, timeout: float = 600.0, job_id: str = "",
                  workdir: Path | None = None) -> tuple[str, str]:
        low = command.lower()
        base = 8.0
        if any(w in low for w in ("test", "suite", "verify", "ci")):
            base = 22.0
        elif any(w in low for w in ("build", "compile", "install")):
            base = 30.0
        elif any(w in low for w in ("repro", "reproduce", "collect")):
            base = 14.0
        duration = base * random.uniform(0.7, 1.3) / self.speed
        await asyncio.sleep(duration)

        if not self.mock:
            # A real model must never mistake a stand-in for a passing test run.
            return "done", (
                f"$ {command}\n(simulated: shell execution is disabled, so this did "
                f"NOT run. It only stood in for {duration:.1f}s of work. Nothing was "
                f"executed and nothing was verified.)"
            )
        if random.random() < 0.12:
            return "failed", (
                f"$ {command}\nexit status 1\nsimulated transient failure after "
                f"{duration:.1f}s"
            )
        return "done", f"$ {command}\nexit status 0\ncompleted in {duration:.1f}s (simulated)"


class HostShell:
    """Unsandboxed. Commands run on this machine with the user's permissions."""

    kind = "host"
    ready = True

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def describe(self) -> str:
        return "host - commands run directly on this machine, unsandboxed"

    async def run(self, command: str, *, timeout: float = 600.0, job_id: str = "",
                  workdir: Path | None = None) -> tuple[str, str]:
        cwd = Path(workdir or self.workspace)
        cwd.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.to_thread(
            subprocess.Popen, command, shell=True, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(asyncio.to_thread(proc.communicate), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return "failed", f"$ {command}\ntimed out after {timeout:.0f}s"
        except asyncio.CancelledError:
            proc.kill()
            raise
        status = "done" if proc.returncode == 0 else "failed"
        return status, f"$ {command}\nexit status {proc.returncode}\n{_decode(stdout)[:8000]}"


class DockerShell:
    """One throwaway container per command."""

    kind = "docker"

    def __init__(self, workspace: Path, *, image: str = SANDBOX_IMAGE,
                 memory: str = "1g", cpus: str = "2") -> None:
        self.workspace = Path(workspace).resolve()
        self.image = image
        self.memory = memory
        self.cpus = cpus
        # Every container is labelled with the workspace it serves, so cleaning
        # up after a crash removes only this instance's leftovers - never the
        # running jobs of another Rimuru (an app, a benchmark) on the same machine.
        self.owner = hashlib.sha256(str(self.workspace).lower().encode()).hexdigest()[:12]
        self.error = ""
        self._ready = threading.Event()
        self._building = False

    # ------------------------------------------------------------------ #
    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def describe(self) -> str:
        if self.error:
            return f"docker - unavailable: {self.error}"
        return "docker - isolated container" + ("" if self.ready else " (preparing image)")

    @staticmethod
    def available() -> bool:
        if not shutil.which("docker"):
            return False
        try:
            probe = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                   capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0 and bool(probe.stdout.strip())

    def cleanup_orphans(self) -> int:
        """Remove sandbox containers a crashed process of this workspace left behind."""
        try:
            ids = subprocess.run(["docker", "ps", "-aq", "--filter", "name=rimuru-",
                                  "--filter", f"label=rimuru.workspace={self.owner}"],
                                 capture_output=True, text=True, timeout=30).stdout.split()
            if ids:
                subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, timeout=60)
            return len(ids)
        except (OSError, subprocess.TimeoutExpired):
            return 0

    def prepare_in_background(self) -> None:
        """Make sure the image exists, building it once if needed (~1 minute)."""
        if self._building or self.ready:
            return
        self._building = True

        def work() -> None:
            try:
                have = subprocess.run(["docker", "image", "inspect", self.image],
                                      capture_output=True, timeout=30)
                if have.returncode != 0:
                    build = subprocess.run(
                        ["docker", "build", "-t", self.image, str(SANDBOX_DIR)],
                        capture_output=True, text=True, timeout=900,
                    )
                    if build.returncode != 0:
                        self.error = "image build failed: " + (build.stderr or build.stdout)[-300:]
                        return
                self._ready.set()
            except Exception as exc:  # pragma: no cover - environment specific
                self.error = f"{type(exc).__name__}: {exc}"
            finally:
                self._building = False

        threading.Thread(target=work, name="sandbox-image", daemon=True).start()

    def _args(self, command: str, name: str, workdir: Path | None = None) -> list[str]:
        args = [
            "docker", "run", "--rm", "--name", name,
            "--label", f"rimuru.workspace={self.owner}",
            "--network", "none",                  # nothing gets in or out
            "--memory", self.memory, "--cpus", self.cpus, "--pids-limit", "256",
            "--read-only", "--tmpfs", "/tmp:rw,size=256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
            # Only this job's folder is visible: the session's own, or the workspace.
            "-v", f"{Path(workdir or self.workspace).resolve()}:/workspace", "-w", "/workspace",
        ]
        if hasattr(os, "getuid"):
            # On Linux hosts, keep files the container writes owned by the user.
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return args + [self.image, "sh", "-c", command]

    async def run(self, command: str, *, timeout: float = 600.0, job_id: str = "",
                  workdir: Path | None = None) -> tuple[str, str]:
        if not self.ready:
            # First use while the image is still being built: wait for it.
            waited = 0.0
            while not self.ready and not self.error and waited < 900:
                await asyncio.sleep(1.0)
                waited += 1.0
            if not self.ready:
                return "failed", f"$ {command}\nsandbox unavailable: {self.error or 'image not ready'}"

        Path(workdir or self.workspace).mkdir(parents=True, exist_ok=True)
        name = f"rimuru-{job_id or os.urandom(4).hex()}"
        proc = await asyncio.to_thread(
            subprocess.Popen, self._args(command, name, workdir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(asyncio.to_thread(proc.communicate), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            # Killing the docker CLI is not enough - the container would keep
            # running. Kill the container itself.
            await asyncio.to_thread(
                subprocess.run, ["docker", "kill", name], capture_output=True, timeout=30,
            )
            proc.kill()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return "failed", f"$ {command}\ntimed out after {timeout:.0f}s (container killed)"
        status = "done" if proc.returncode == 0 else "failed"
        return status, f"$ {command}\nexit status {proc.returncode}\n{_decode(stdout)[:8000]}"


def make_shell(config: Any, *, speed: float = 1.0):
    """Pick the backend. Returns (shell, note)."""
    mock = config.is_demo()      # the demo model issues pretend commands
    wanted = (config.shell or "auto").lower()

    if config.enable_bash or wanted == "host":
        return HostShell(config.workspace), "shell: host (unsandboxed - commands run on this machine)"
    if mock or wanted == "simulated":
        reason = "the mock model issues fake commands" if mock else "configured"
        return SimulatedShell(mock=mock, speed=speed), f"shell: simulated ({reason})"

    if DockerShell.available():
        shell = DockerShell(config.workspace)
        orphans = shell.cleanup_orphans()
        shell.prepare_in_background()
        return shell, "shell: docker sandbox (isolated, no network)" + (
            f" - removed {orphans} container(s) left by a crash" if orphans else "")
    if wanted == "docker":
        raise RuntimeError("RIMURU_SHELL=docker but the Docker engine is not reachable")
    return SimulatedShell(mock=mock, speed=speed), (
        "shell: simulated (Docker is not running - start Docker Desktop to let workers run code)"
    )
