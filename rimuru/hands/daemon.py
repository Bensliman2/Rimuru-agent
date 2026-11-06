"""The remote hands: run this on the other machine.

    python run.py hands --connect http://<Rimuru's PC>:8810 --code ABCD2345 --root C:\\work

It connects OUT to Rimuru - nothing opens on this machine - pairs once with the
code from Rimuru's Connections, and keeps the secret it is given in
~/.rimuru-hands.json for next time. Then it does only this, only inside the
folders given with --root:

  dirs     list folders (for picking a session's folder)
  list, read, write    a session's files
  run, stop, install   commands - in this machine's Docker sandbox; without
                       Docker, none at all, unless started with --allow-host-commands
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

from ..worker.files import LocalFiles, Refused
from ..worker.shell import DockerShell, HostShell, check_packages

STATE = Path.home() / ".rimuru-hands.json"
DATA = Path.home() / ".rimuru-hands"


class Hands:
    def __init__(self, roots: list[str], *, allow_host: bool) -> None:
        self.roots = [Path(r).expanduser().resolve() for r in roots]
        missing = [str(r) for r in self.roots if not r.is_dir()]
        if not self.roots or missing:
            raise SystemExit(f"Share at least one existing folder with --root (not found: {', '.join(missing)})")
        self.shell: Any = None
        if DockerShell.available():
            self.shell = DockerShell(self.roots[0])
            self.shell.prepare_in_background()
        elif allow_host:
            self.shell = HostShell(self.roots[0])
        self.jobs: dict[str, asyncio.Task] = {}

    @property
    def docker(self) -> bool:
        return isinstance(self.shell, DockerShell)

    def inside(self, folder: str) -> Path:
        """A folder this machine shares - or Refused."""
        path = Path(folder).expanduser().resolve()
        for root in self.roots:
            try:
                path.relative_to(root)
                return path
            except ValueError:
                continue
        raise Refused("that folder is outside the folders this machine shares")

    def deps(self, folder: Path) -> Path:
        return DATA / "deps" / hashlib.sha256(str(folder).lower().encode()).hexdigest()[:16]

    async def handle(self, op: str, msg: dict[str, Any]) -> Any:
        if op == "dirs":
            if not msg.get("path"):
                return {"path": "", "parent": None, "dirs": [{"name": str(r), "path": str(r)} for r in self.roots]}
            path = self.inside(str(msg["path"]))
            dirs = sorted((p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")),
                          key=lambda p: p.name.lower())[:400]
            parent = None if path in self.roots else str(path.parent)
            return {"path": str(path), "parent": parent,
                    "dirs": [{"name": p.name, "path": str(p)} for p in dirs]}
        if op == "mkdir":
            path = self.inside(str(msg.get("path") or ""))
            path.mkdir(parents=True, exist_ok=True)
            return {"path": str(path)}
        folder = self.inside(str(msg.get("folder") or ""))
        files = LocalFiles(folder)
        if op == "list":
            return await files.list(str(msg.get("rel") or "."))
        if op == "read":
            return await files.read(str(msg.get("rel") or ""))
        if op == "write":
            await files.write(str(msg.get("rel") or ""), str(msg.get("text") or ""))
            return True
        if op in {"run", "install"} and self.shell is None:
            raise RuntimeError("this machine runs no commands: Docker is not running here, and it "
                               "was not started with --allow-host-commands")
        if op == "run":
            job_id = str(msg.get("job_id") or os.urandom(4).hex())
            task = asyncio.ensure_future(self.shell.run(
                str(msg.get("command") or ""), timeout=float(msg.get("run_timeout") or 600),
                job_id=job_id, workdir=folder, deps=self.deps(folder)))
            self.jobs[job_id] = task
            try:
                status, output = await task
            finally:
                self.jobs.pop(job_id, None)
            return {"status": status, "output": output}
        if op == "install":
            if not self.docker:
                raise RuntimeError("installing needs Docker on this machine")
            packages = [str(p) for p in msg.get("packages") or []]
            problem = check_packages(packages, str(msg.get("kind") or ""))
            if problem:
                raise RuntimeError(problem)
            status, output = await self.shell.install(packages, str(msg["kind"]), self.deps(folder))
            return {"status": status, "output": output}
        if op == "stop":
            task = self.jobs.get(str(msg.get("job_id") or ""))
            if task is not None:
                task.cancel()                  # DockerShell kills the container
            return True
        raise RuntimeError(f"unknown request {op!r}")


def _load() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(STATE, 0o600)


async def serve(connect: str, *, code: str | None, roots: list[str], name: str,
                allow_host: bool, once: bool = False, state_file: Path | None = None) -> int:
    """Connect to Rimuru and do its requests, reconnecting when the line drops."""
    import websockets

    global STATE
    if state_file is not None:
        STATE = state_file
    url = connect.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    url = url if url.endswith("/ws/hands") else url + "/ws/hands"
    hands = Hands(roots, allow_host=allow_host)
    wait = 2.0
    while True:
        saved = _load().get(url)
        hello: dict[str, Any] = {"type": "hello", "name": name, "roots": [str(r) for r in hands.roots],
                                 "docker": hands.docker,
                                 "host_commands": isinstance(hands.shell, HostShell)}
        if saved:
            hello.update(machine_id=saved["machine_id"], secret=saved["secret"])
        elif code:
            hello["code"] = code
        else:
            print("Not paired yet: get a code in Rimuru -> Connections -> Add a machine, and pass --code.")
            return 2
        try:
            async with websockets.connect(url, max_size=8 * 1024 * 1024, open_timeout=15) as ws:
                await ws.send(json.dumps(hello))
                reply = json.loads(await ws.recv())
                if reply.get("type") != "welcome":
                    print(f"Rimuru said no: {reply.get('reason', 'refused')}")
                    if saved:                       # removed in Rimuru: forget the old secret
                        state = _load()
                        state.pop(url, None)
                        _save(state)
                    return 1
                if reply.get("secret"):
                    state = _load()
                    state[url] = {"machine_id": reply["machine_id"], "secret": reply["secret"]}
                    _save(state)
                print(f"Connected to Rimuru as \"{name}\". Sharing: {', '.join(map(str, hands.roots))}. "
                      f"Commands: {'in Docker' if hands.docker else 'on this machine' if hands.shell else 'none'}.")
                wait = 2.0
                tasks: set[asyncio.Task] = set()

                async def answer(msg: dict[str, Any]) -> None:
                    try:
                        result = await hands.handle(str(msg.get("op")), msg)
                        out = {"id": msg.get("id"), "ok": True, "result": result}
                    except Refused as exc:
                        out = {"id": msg.get("id"), "ok": False, "error": str(exc)}
                    except Exception as exc:  # noqa: BLE001 - say what went wrong, keep serving
                        out = {"id": msg.get("id"), "ok": False, "error": f"{exc}"[:500]}
                    if msg.get("id"):
                        await ws.send(json.dumps(out))

                async for raw in ws:
                    task = asyncio.ensure_future(answer(json.loads(raw)))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as exc:
            print(f"Lost Rimuru ({type(exc).__name__}); trying again in {wait:.0f}s.")
        if once:
            return 0
        await asyncio.sleep(wait)
        wait = min(wait * 2, 60.0)


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="run.py hands", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connect", required=True, help="Rimuru's address, e.g. http://192.168.1.20:8810")
    parser.add_argument("--code", default=None, help="the pairing code from Rimuru's Connections")
    parser.add_argument("--root", action="append", required=True,
                        help="a folder to share (repeat for several)")
    parser.add_argument("--name", default=socket.gethostname(), help="how this machine is called in Rimuru")
    parser.add_argument("--allow-host-commands", action="store_true",
                        help="without Docker, run commands directly on this machine (unsandboxed)")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(serve(args.connect, code=args.code, roots=args.root, name=args.name,
                                 allow_host=args.allow_host_commands))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
