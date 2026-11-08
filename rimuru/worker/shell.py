"""Where a worker's commands actually run.

Three backends:

  docker     An isolated Linux container per command: Python 3.12 with common
             packages and Node.js (sandbox/Dockerfile), the workspace mounted as
             the working directory, no network, a read-only root filesystem, every
             capability dropped, and caps on memory, CPU and process count. The
             model's commands can touch the workspace and nothing else. Packages
             a job needs beyond those are installed in a SEPARATE container that
             has the internet but sees only the job's package folder - never the
             person's files - and arrive in work containers read-only.
             This is the default whenever Docker is available.

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
import re
import shutil
import subprocess
import threading
import tempfile
from pathlib import Path
from typing import Any

from ..safety import secret_paths, without_secrets

SANDBOX_IMAGE = "rimuru-sandbox:2"          # a new tag rebuilds the image on the next start
SANDBOX_DIR = Path(__file__).resolve().parents[2] / "sandbox"


def _decode(raw: bytes | None) -> str:
    return (raw or b"").decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# packages installed on demand
# --------------------------------------------------------------------------- #
# A name and, optionally, a version - never a URL, a path or an option: those
# could point the installer at anything ("--index-url ...", "git+https://...").
_PYTHON_PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}(\[[A-Za-z0-9_,.-]{1,60}\])?"
                         r"((==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!_-]{1,40})?$")
_NODE_PKG = re.compile(r"^(@[a-z0-9][a-z0-9._~-]{0,60}/)?[a-z0-9][a-z0-9._~-]{0,80}"
                       r"(@[A-Za-z0-9.^~<>=*+_-]{1,40})?$")


def check_packages(names: list[str], kind: str) -> str | None:
    """Why these cannot be installed, or None when they can."""
    if kind not in {"python", "node"}:
        return 'kind must be "python" or "node".'
    if not names:
        return "no package names given."
    if len(names) > 10:
        return "at most 10 packages at a time."
    pattern = _PYTHON_PKG if kind == "python" else _NODE_PKG
    bad = [n for n in names if not pattern.match(n)]
    if bad:
        return (f"{', '.join(bad[:3])} is not a plain package name (with an optional version). "
                f"URLs, paths and options are not allowed.")
    return None


def deps_dir_for(config: Any, folder: Path) -> Path:
    """Where the packages installed for work in `folder` are kept - outside it,
    with Rimuru's data, one set per work folder."""
    key = hashlib.sha256(str(Path(folder).resolve()).lower().encode()).hexdigest()[:16]
    return Path(config.db_path).resolve().parent / "deps" / key


class SimulatedShell:
    kind = "simulated"
    ready = True

    def __init__(self, *, mock: bool, speed: float = 1.0) -> None:
        self.mock = mock
        self.speed = max(speed, 0.001)

    def describe(self) -> str:
        return "simulated - nothing actually runs"

    async def run(self, command: str, *, timeout: float = 600.0, job_id: str = "",
                  workdir: Path | None = None, deps: Path | None = None) -> tuple[str, str]:
        if not self.mock:
            # A real model must never mistake a stand-in for a passing test run -
            # and must not wait for it either. Found in the user test: with Docker
            # off, every command still sat 8-30 seconds doing nothing.
            return "done", (
                f"$ {command}\n(simulated: shell execution is disabled, so this did "
                f"NOT run. Nothing was executed and nothing was verified.)"
            )
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
                  workdir: Path | None = None, deps: Path | None = None) -> tuple[str, str]:
        cwd = Path(workdir or self.workspace)
        cwd.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.to_thread(
            subprocess.Popen, command, shell=True, cwd=str(cwd), env=without_secrets(),
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
                 memory: str = "1g", cpus: str = "2", blank_dir: Path | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        # Where the empty stand-in for secret files lives. In Docker Compose this
        # must be a folder the sandbox service shares (the data folder).
        self.blank_dir = Path(blank_dir or tempfile.gettempdir())
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

    def _base(self, name: str, network: str) -> list[str]:
        args = [
            "docker", "run", "--rm", "--name", name,
            "--label", f"rimuru.workspace={self.owner}",
            "--network", network,
            "--memory", self.memory, "--cpus", self.cpus, "--pids-limit", "256",
            "--read-only", "--tmpfs", "/tmp:rw,size=512m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
        ]
        if hasattr(os, "getuid"):
            # On Linux hosts, keep files the container writes owned by the user.
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return args

    def _args(self, command: str, name: str, workdir: Path | None = None,
              deps: Path | None = None) -> list[str]:
        folder = Path(workdir or self.workspace).resolve()
        # Nothing gets in or out; only this job's folder is visible.
        args = self._base(name, "none") + ["-v", f"{folder}:/workspace", "-w", "/workspace"]
        # Files that hold keys are not there for a command: an empty file stands
        # in for each, and an empty folder for the likes of .ssh.
        for rel, is_folder in secret_paths(folder):
            args += ["--tmpfs", f"/workspace/{rel}"] if is_folder else ["-v", f"{self._blank()}:/workspace/{rel}:ro"]
        if deps is not None and Path(deps).is_dir():
            # Packages installed for this folder (install()), read-only.
            args += ["-v", f"{Path(deps).resolve()}:/deps:ro",
                     "-e", "PYTHONPATH=/deps/python", "-e", "NODE_PATH=/deps/node/node_modules",
                     "-e", "PATH=/deps/node/node_modules/.bin:/usr/local/bin:/usr/bin:/bin"]
            modules = Path(deps) / "node" / "node_modules"
            own = folder / "node_modules"
            if modules.is_dir() and not (own.is_dir() and any(own.iterdir())):
                # Where Node looks first. Not over a project's own node_modules.
                args += ["-v", f"{modules.resolve()}:/workspace/node_modules:ro"]
        return args + [self.image, "sh", "-c", command]

    def _blank(self) -> Path:
        self.blank_dir.mkdir(parents=True, exist_ok=True)
        path = (self.blank_dir / ".rimuru-blank").resolve()
        if not path.exists():
            path.touch()
        return path

    def _install_args(self, packages: list[str], kind: str, name: str, deps: Path) -> list[str]:
        """Has the internet, and sees ONLY the package folder: never the person's
        files, so nothing it runs can read them. And nothing a package ships runs
        while installing: Python takes ready-made wheels only, npm runs no scripts."""
        if kind == "python":
            command = ["pip", "install", "--no-cache-dir", "--disable-pip-version-check",
                       "--only-binary=:all:", "--target", "/deps/python", *packages]
        else:
            command = ["npm", "install", "--prefix", "/deps/node", "--ignore-scripts",
                       "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org/", *packages]
        return (self._base(name, "bridge")
                + ["-v", f"{Path(deps).resolve()}:/deps", "-w", "/deps", self.image, *command])

    async def _wait_ready(self) -> str | None:
        if not self.ready:
            # First use while the image is still being built: wait for it.
            waited = 0.0
            while not self.ready and not self.error and waited < 900:
                await asyncio.sleep(1.0)
                waited += 1.0
        return None if self.ready else (self.error or "image not ready")

    async def install(self, packages: list[str], kind: str, deps: Path, *,
                      timeout: float = 300.0) -> tuple[str, str]:
        label = f"install ({kind}): {' '.join(packages)}"
        problem = check_packages(packages, kind)
        if problem:
            return "failed", f"{label}\nnot installed: {problem}"
        unavailable = await self._wait_ready()
        if unavailable:
            return "failed", f"{label}\nsandbox unavailable: {unavailable}"
        (Path(deps) / kind).mkdir(parents=True, exist_ok=True)
        name = f"rimuru-install-{os.urandom(4).hex()}"
        return await self._execute(self._install_args(packages, kind, name, deps), name, label, timeout)

    async def run(self, command: str, *, timeout: float = 600.0, job_id: str = "",
                  workdir: Path | None = None, deps: Path | None = None) -> tuple[str, str]:
        unavailable = await self._wait_ready()
        if unavailable:
            return "failed", f"$ {command}\nsandbox unavailable: {unavailable}"
        Path(workdir or self.workspace).mkdir(parents=True, exist_ok=True)
        name = f"rimuru-{job_id or os.urandom(4).hex()}"
        return await self._execute(self._args(command, name, workdir, deps), name,
                                   f"$ {command}", timeout)

    async def _execute(self, args: list[str], name: str, label: str,
                       timeout: float) -> tuple[str, str]:
        proc = await asyncio.to_thread(
            subprocess.Popen, args,
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
            return "failed", f"{label}\ntimed out after {timeout:.0f}s (container killed)"
        status = "done" if proc.returncode == 0 else "failed"
        return status, f"{label}\nexit status {proc.returncode}\n{_decode(stdout)[:8000]}"


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
        shell = DockerShell(config.workspace, blank_dir=Path(config.db_path).resolve().parent)
        orphans = shell.cleanup_orphans()
        shell.prepare_in_background()
        return shell, "shell: docker sandbox (isolated, no network)" + (
            f" - removed {orphans} container(s) left by a crash" if orphans else "")
    if wanted == "docker":
        raise RuntimeError("RIMURU_SHELL=docker but the Docker engine is not reachable")
    return SimulatedShell(mock=mock, speed=speed), (
        "shell: simulated (Docker is not running - start Docker Desktop to let workers run code)"
    )
