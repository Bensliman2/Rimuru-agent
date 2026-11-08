"""What the API's features share: how to find a session and where it works."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .. import sessions as chats
from ..hands import remote


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
        return {"kind": kind, "name": path.name, "path": str(path), "shown": chats.display(path),
                "exists": path.is_dir(), "working": self.busy_in(thread)}

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
