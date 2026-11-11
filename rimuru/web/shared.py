"""What the API's features share: how to find a session and where it works."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from .. import sessions as chats
from ..hands import remote


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
HERE = {"127.0.0.1", "::1", "localhost", "testclient"}


def host_of(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.split(":", 1)[0]


class Guard:
    """Who may ask. The app stores API keys and can point models at any server,
    so it answers only this computer: no other website may drive it (cross-site
    requests), and no other host name may reach it (DNS rebinding). Serving on a
    network address (RIMURU_HOST=0.0.0.0) is a deliberate choice;
    RIMURU_ALLOWED_HOSTS lists extra names to accept."""

    def __init__(self, config: Any) -> None:
        extra = {h.strip().lower() for h in os.environ.get("RIMURU_ALLOWED_HOSTS", "").split(",") if h.strip()}
        self.allowed = LOCAL_HOSTS | {config.host.lower()} | extra
        self.open_host = config.host in {"0.0.0.0", "::"}
        # Unless the app is served beyond this computer on purpose, only this
        # computer gets an answer - by the caller's address, not only the Host
        # header. (Other machines reach Rimuru only through the hands' own port.)
        self.beyond = self.open_host or bool(extra)

    def refuse(self, headers: Any, method: str, caller: str | None = None) -> str | None:
        host = headers.get("host", "")
        if not self.beyond and caller and caller not in HERE and not caller.startswith("127."):
            return "This app only answers on this computer."
        if not self.open_host and host_of(host) not in self.allowed:
            return "This app only answers on this computer."
        origin = headers.get("origin")
        if origin and method not in {"GET", "HEAD", "OPTIONS"} and \
                urlparse(origin).netloc.lower() != host.lower():
            return "Requests from other websites are refused."
        return None


def channel_of(key: str) -> str:
    return "telegram" if key.startswith("tg-") else "console"


def session_key(raw: Any) -> str:
    key = str(raw or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key):
        raise HTTPException(400, "Which session? Its key is missing or malformed.")
    return key


class Web:
    """The running system, as the routes see it."""

    def __init__(self, system: Any) -> None:
        self.system = system
        self._folder_looks: dict[str, tuple[Any, float, tuple]] = {}

    def session_thread(self, key: str):
        """A session by its key - on the chat page, or a Telegram chat (tg-...)."""
        by_key = self.system.store.thread_by_key
        for channel in sorted(self.system.config.conversation_channels):
            if (thread_id := by_key.get(f"{channel}:{key}")) is not None:
                return self.system.store.threads.get(thread_id)
        return None

    def in_session(self, task, key: str) -> bool:
        thread = self.system.store.threads.get(task.thread_id or "")
        return (thread is not None and thread.channel in self.system.config.conversation_channels
                and thread.key == key)

    def busy_in(self, thread) -> bool:
        return thread is not None and any(t.thread_id == thread.id and t.status in {"running", "queued"}
                                          for t in self.system.store.tasks.values())

    def folder_info(self, key: str) -> dict[str, Any]:
        """Where a session works, for the folder bar above the chat."""
        system = self.system
        thread = self.session_thread(key)
        kind = chats.folder_kind(thread) if thread is not None else "new"
        if kind == "remote":
            machine_id, where = remote.parse(thread.folder) or ("", "")
            online = machine_id in system.hands.links
            return {"kind": "remote", "name": re.split(r"[\\/]", where.rstrip("\\/"))[-1] or where,
                    "path": where, "shown": where, "exists": False, "working": self.busy_in(thread),
                    "machine": system.hands.name(machine_id), "online": online}
        path = chats.folder_for(system.config, thread) if thread is not None else None
        if kind == "new" and path is not None and path.exists():
            kind = "made"                # a session from before folders were stored
        if kind == "new" or path is None:
            return {"kind": "new", "name": "", "path": "", "shown": "", "exists": False,
                    "working": self.busy_in(thread)}
        page = next((n for n in ("index.html", "index.htm") if (path / n).is_file()), "")
        return {"kind": kind, "name": path.name, "path": str(path), "shown": chats.display(path),
                "exists": path.is_dir(), "working": self.busy_in(thread), "view": page}

    def task_folder(self, task) -> Path:
        if task is not None and task.folder:
            return Path(task.folder)
        return chats.folder_for(self.system.config,
                                self.system.store.threads.get((task.thread_id if task else None) or ""))

    def folder_look(self, thread, jobs: int, working: int) -> tuple:
        """A session's folder: where it is, whether it holds anything, whether it
        is Rimuru's own (only that one can be deleted with the session). On disk
        at most every few seconds per session - sooner if its work changes."""
        key = (thread.folder, thread.rimuru_folder, jobs, working)
        seen = self._folder_looks.get(thread.id)
        if seen and seen[0] == key and time.time() - seen[1] < 10.0:
            return seen[2]
        config = self.system.config
        folder = chats.folder_for(config, thread)
        look = (folder, chats.display(folder), folder.exists() and any(folder.iterdir()),
                chats.folder_kind(thread) in {"made", "new"} and folder.is_dir()
                and folder.resolve().is_relative_to(config.sessions_dir.resolve()))
        self._folder_looks[thread.id] = (key, time.time(), look)
        return look

    def last_said(self, thread) -> float:
        lines = self.system.store.chat_of(thread.key)
        return max(thread.last_ts, lines[-1]["ts"]) if lines else thread.last_ts
