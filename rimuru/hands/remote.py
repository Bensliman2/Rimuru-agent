"""A folder on another machine, as a clone sees it: files and commands go there.

A session's folder on another machine is written "hands://<machine>/<path>".
"""
from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import Any

from ..worker.files import Refused, SecretFile

PREFIX = "hands://"


def ref(machine_id: str, path: str) -> str:
    return f"{PREFIX}{machine_id}/{path}"


def parse(folder: str) -> tuple[str, str] | None:
    """(machine_id, path on that machine) - or None for a folder on this computer."""
    if not str(folder or "").startswith(PREFIX):
        return None
    machine_id, _, path = str(folder)[len(PREFIX):].partition("/")
    return (machine_id, path) if machine_id and path else None


class RemoteFiles:
    def __init__(self, hub: Any, machine_id: str, folder: str) -> None:
        self.hub, self.machine_id, self.folder = hub, machine_id, folder

    @staticmethod
    def name(rel: str) -> str:
        parts = [p for p in PurePosixPath(str(rel or ".").replace("\\", "/")).parts if p not in {".", ""}]
        if ".." in parts:
            raise Refused("that path is outside the workspace")
        return "/".join(parts) or "."

    async def _ask(self, op: str, **args: Any) -> Any:
        from .hub import RemoteError

        try:
            return await self.hub.request(self.machine_id, op, folder=self.folder, **args)
        except RemoteError as exc:
            if "holds secrets" in str(exc):
                raise SecretFile(str(exc)) from None
            if "outside" in str(exc):
                raise Refused(str(exc)) from None
            raise

    async def list(self, rel: str = ".") -> list[str] | None:
        return await self._ask("list", rel=self.name(rel))

    async def read(self, rel: str) -> str | None:
        return await self._ask("read", rel=self.name(rel))

    async def write(self, rel: str, text: str) -> None:
        await self._ask("write", rel=self.name(rel), text=text, timeout=120)


class RemoteShell:
    """Commands in that machine's own sandbox (or not at all - its choice)."""

    def __init__(self, hub: Any, machine_id: str, folder: str, *, docker: bool) -> None:
        self.hub, self.machine_id, self.folder = hub, machine_id, folder
        # A clone gets bash/install only where they can actually run.
        self.kind = "docker" if docker else "remote"
        self.ready = True

    def describe(self) -> str:
        return f"on {self.hub.name(self.machine_id)}"

    async def run(self, command: str, *, timeout: float = 600.0, job_id: str = "",
                  workdir: Path | None = None, deps: Path | None = None) -> tuple[str, str]:
        try:
            answer = await self.hub.request(self.machine_id, "run", timeout=timeout + 30,
                                            folder=self.folder, command=command,
                                            run_timeout=timeout, job_id=job_id)
        except asyncio.CancelledError:
            # Stopped here: stop it there too - the container, not only the wait.
            await self.hub.send(self.machine_id, "stop", job_id=job_id)
            raise
        except Exception as exc:  # noqa: BLE001
            return "failed", f"$ {command}\n{exc}"
        return str(answer.get("status") or "failed"), str(answer.get("output") or "")

    async def install(self, packages: list[str], kind: str, deps: Path, *,
                      timeout: float = 300.0) -> tuple[str, str]:
        try:
            answer = await self.hub.request(self.machine_id, "install", timeout=timeout + 30,
                                            folder=self.folder, packages=packages, kind=kind)
        except Exception as exc:  # noqa: BLE001
            return "failed", str(exc)
        return str(answer.get("status") or "failed"), str(answer.get("output") or "")
