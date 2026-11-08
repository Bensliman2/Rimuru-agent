"""Rimuru's side of the remote hands: pairing, the listener, and requests.

The listener is a separate little server on its own port, serving only
/ws/hands - the app and its API keep answering this computer only. A browser
cannot use it either: a connection that carries an Origin header (every page
sends one) is refused, so no web page can pair with or drive a machine.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import itertools
import json
import logging
import os
import secrets
import socket
import time
from pathlib import Path
from typing import Any

# At module level: the route's annotation must resolve (annotations are postponed).
from fastapi import FastAPI, WebSocket

from ..logs import noted

log = logging.getLogger("rimuru.hands")

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I: read aloud safely
CODE_LIFE = 600.0
MAX_FAILURES = 5


class Unavailable(RuntimeError):
    """The machine is not connected right now."""


class RemoteError(RuntimeError):
    """The machine refused or failed the request; the message says why."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def lan_address() -> str:
    """This computer's address on the local network (no packet is sent)."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    return "127.0.0.1"


class _Link:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.pending: dict[int, asyncio.Future] = {}


class HandsHub:
    def __init__(self, config: Any, bus: Any) -> None:
        self.bus = bus
        self.path = Path(config.db_path).resolve().parent / "hands.json"
        try:
            self.machines: dict[str, dict[str, Any]] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.machines = {}
        self.host = os.environ.get("RIMURU_HANDS_HOST", "0.0.0.0")
        self.port = int(os.environ.get("RIMURU_HANDS_PORT", "8810"))
        self.codes: dict[str, float] = {}
        self.links: dict[str, _Link] = {}
        self.failures: dict[str, list[float]] = {}
        self._ids = itertools.count(1)
        self._server: Any = None
        self._serving: asyncio.Task | None = None
        self.listen_error = ""

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.machines, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------ #
    # the listener
    # ------------------------------------------------------------------ #
    def app(self) -> Any:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.websocket("/ws/hands")
        async def hands(ws: WebSocket) -> None:
            await self.serve(ws)

        return app

    async def listen(self) -> None:
        """Start the listener (once). Called when a machine is paired or exists."""
        if self._serving is not None and not self._serving.done():
            return
        import uvicorn

        class Quiet(uvicorn.Server):
            # The app's own server handles Ctrl+C; this one must not take it over.
            @contextlib.contextmanager
            def capture_signals(self):  # noqa: ANN202
                yield

            def install_signal_handlers(self) -> None:
                return None

        self._server = Quiet(uvicorn.Config(self.app(), host=self.host, port=self.port,
                                            log_level="warning", ws_max_size=8 * 1024 * 1024))
        self._serving = asyncio.create_task(self._server.serve(), name="hands-listener")
        await asyncio.sleep(0.3)
        if self._serving.done():
            exc = self._serving.exception() if not self._serving.cancelled() else None
            self.listen_error = f"could not listen on port {self.port}" + (f": {exc}" if exc else "")
        else:
            self.listen_error = ""

    async def stop(self) -> None:
        for link in list(self.links.values()):
            with noted("closing a machine's connection", logging.DEBUG):
                await link.ws.close()
        if self._server is not None:
            self._server.should_exit = True
        if self._serving is not None:
            with noted("stopping the machines' port", logging.DEBUG):
                await asyncio.wait_for(self._serving, 5)

    # ------------------------------------------------------------------ #
    # pairing
    # ------------------------------------------------------------------ #
    def new_code(self) -> str:
        now = time.time()
        self.codes = {c: t for c, t in self.codes.items() if t > now}
        code = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
        self.codes[code] = now + CODE_LIFE
        return code

    def command(self, code: str) -> str:
        return (f"python run.py hands --connect http://{lan_address()}:{self.port} "
                f"--code {code} --root <the folder to share>")

    def _blocked(self, peer: str) -> bool:
        recent = [t for t in self.failures.get(peer, []) if t > time.time() - 600]
        self.failures[peer] = recent
        return len(recent) >= MAX_FAILURES

    async def serve(self, ws: Any) -> None:
        """One machine's connection, from hello to goodbye."""
        peer = getattr(getattr(ws, "client", None), "host", "?")
        if ws.headers.get("origin") or self._blocked(peer):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 15))
        except Exception:  # noqa: BLE001
            log.info("a machine connected and never said who it was", exc_info=True)
            await ws.close(code=1008)
            return

        machine_id, reply = self._admit(hello)
        if machine_id is None:
            self.failures.setdefault(peer, []).append(time.time())
            await ws.send_text(json.dumps({"type": "refused", "reason": reply["reason"]}))
            await ws.close(code=1008)
            return
        await ws.send_text(json.dumps(reply))

        link = self.links[machine_id] = _Link(ws)
        self.bus.emit("hands.connected", machine_id=machine_id,
                      name=self.machines[machine_id]["name"])
        try:
            while True:
                message = json.loads(await ws.receive_text())
                future = link.pending.pop(int(message.get("id") or 0), None)
                if future is not None and not future.done():
                    future.set_result(message)
        except Exception:  # noqa: BLE001 - disconnected
            log.info("machine %s disconnected", machine_id, exc_info=True)
        finally:
            if self.links.get(machine_id) is link:
                del self.links[machine_id]
            for future in link.pending.values():
                if not future.done():
                    future.set_exception(Unavailable(f"{self.machines.get(machine_id, {}).get('name', 'the machine')} disconnected"))
            self.machines.get(machine_id, {})["last_seen"] = time.time()
            self._save()
            self.bus.emit("hands.disconnected", machine_id=machine_id)

    def _admit(self, hello: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        info = {
            "name": " ".join(str(hello.get("name") or "a machine").split())[:40],
            "roots": [str(r) for r in hello.get("roots") or []][:10],
            "docker": bool(hello.get("docker")),
            "host_commands": bool(hello.get("host_commands")),
            "last_seen": time.time(),
        }
        code = str(hello.get("code") or "").strip().upper()
        if code:
            expiry = self.codes.pop(code, 0)
            if expiry < time.time():
                return None, {"reason": "That pairing code is wrong or has expired - make a new one in Connections."}
            machine_id = "mch_" + secrets.token_hex(4)
            secret = secrets.token_urlsafe(32)
            self.machines[machine_id] = {**info, "secret_hash": _hash(secret), "paired": time.time()}
            self._save()
            return machine_id, {"type": "welcome", "machine_id": machine_id, "secret": secret}
        machine_id = str(hello.get("machine_id") or "")
        known = self.machines.get(machine_id)
        if known is None or not hmac.compare_digest(known["secret_hash"], _hash(str(hello.get("secret") or ""))):
            return None, {"reason": "This machine is not paired (or was removed). Pair it again with a new code."}
        known.update(info)
        self._save()
        return machine_id, {"type": "welcome", "machine_id": machine_id}

    # ------------------------------------------------------------------ #
    # using a machine
    # ------------------------------------------------------------------ #
    def name(self, machine_id: str) -> str:
        return self.machines.get(machine_id, {}).get("name", "another machine")

    async def request(self, machine_id: str, op: str, timeout: float = 60.0, **args: Any) -> Any:
        link = self.links.get(machine_id)
        if link is None:
            raise Unavailable(f"{self.name(machine_id)} is not connected right now")
        rid = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        link.pending[rid] = future
        await link.ws.send_text(json.dumps({"id": rid, "op": op, **args}))
        try:
            answer = await asyncio.wait_for(future, timeout)
        finally:
            link.pending.pop(rid, None)
        if not answer.get("ok"):
            raise RemoteError(str(answer.get("error") or "it failed"))
        return answer.get("result")

    async def send(self, machine_id: str, op: str, **args: Any) -> None:
        """No answer needed (e.g. stop a job)."""
        link = self.links.get(machine_id)
        if link is not None:
            with noted(f"telling a machine to {op}"):
                await link.ws.send_text(json.dumps({"id": 0, "op": op, **args}))

    async def remove(self, machine_id: str) -> None:
        self.machines.pop(machine_id, None)
        self._save()
        link = self.links.pop(machine_id, None)
        if link is not None:
            with noted("closing a removed machine's connection", logging.DEBUG):
                await link.ws.close()

    def status(self) -> dict[str, Any]:
        return {
            "listening": self._serving is not None and not self._serving.done(),
            "port": self.port, "problem": self.listen_error,
            "machines": [{"id": mid, "name": m["name"], "online": mid in self.links,
                          "roots": m.get("roots", []), "docker": m.get("docker", False),
                          "host_commands": m.get("host_commands", False),
                          "last_seen": m.get("last_seen")}
                         for mid, m in sorted(self.machines.items(), key=lambda kv: kv[1]["name"])],
        }
