"""Sessions: separate conversations with Rimuru, each with its own clones and files.

A session is one conversation thread on a chat channel ("console"). Rimuru is
still one assistant across all of them - it remembers across sessions - but each
session's work is kept apart: its clones are listed only there, and they work in
the session's own folder, so two sessions never touch each other's files.

The first conversation ever ("direct") predates sessions and keeps the original
workspace folder. Every other session gets data/sessions/<name>-<id>/, outside
the shared workspace, so a practice run that resets the workspace cannot wipe it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEFAULT = "direct"


def title_from(text: str, limit: int = 60) -> str:
    """A session's name, from the first thing the person asked."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text or "New session"
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(",.;:-") + "…"


def is_session(thread: Any, config: Any) -> bool:
    return thread is not None and thread.channel in config.conversation_channels


def folder_name(thread: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (thread.title or "").lower()).strip("-")[:40].strip("-")
    tail = re.sub(r"[^a-z0-9]", "", thread.key.lower())[-6:] or "session"
    return f"{slug}-{tail}" if slug else tail


def folder_for(config: Any, thread: Any) -> Path:
    """Where a clone working for this thread keeps its files."""
    if not is_session(thread, config) or thread.key == DEFAULT:
        return Path(config.workspace).resolve()
    return (Path(config.sessions_dir) / folder_name(thread)).resolve()


def title_of(store: Any, thread: Any) -> str:
    if thread.key != DEFAULT and thread.title:
        return thread.title
    first = next((m["text"] for m in store.chat
                  if m["role"] == "user" and m.get("chat", DEFAULT) == thread.key), "")
    return title_from(first) if first else "Your first session"
