"""Long-term memory (article s8.1): what Rimuru remembers across sessions and restarts.

Before, the Router could write notes but nothing ever read them back - memory
was write-only. Now:
  - Rimuru remembers lasting facts by itself (remember / forget), one per call:
    about the person, about one of their sessions, or general.
  - What may matter comes back to it with each turn: everything about the person,
    the memories of the session(s) the turn is about, and general ones that share
    words with it - each shown once per stretch of context, not every turn.
  - Clones get the person's preferences and their session's memories in the brief.
  - The person sees every memory being made (a line in the chat) and can read,
    change and delete all of them (Under the hood -> Memory).
  - Secrets are never kept: anything that looks like a key or a password is refused.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .models import Memory

# Keys and passwords: never written into long-term memory.
_SECRET = re.compile(
    r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}"
    r"|\b(ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bxox[abpr]-[A-Za-z0-9-]{10,}"
    r"|\b\d{8,10}:[A-Za-z0-9_-]{30,}"                 # a Telegram bot token
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(password|passcode|passwd|pin code|api[ _-]?key|secret key|access token|token)\b"
    r"\s*(is|was|=|:)\s*\S+",
    re.IGNORECASE)
_RANDOM = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b")

_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")
_COMMON = set("""the and for that this with you your are was were have has had not but they them
their there what when where which who will would can could should about into from then than
also just like some more most very only make made want need please thanks hello okay sure
it's i'm don't can't one two its our out all any how why did does done new get got use used""".split())

ABOUT = {"you": "about the person", "session": "about this session", "general": ""}


def looks_secret(text: str) -> bool:
    return bool(_SECRET.search(text or "") or _RANDOM.search(text or ""))


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _COMMON}


def same(a: str, b: str) -> bool:
    norm = lambda t: " ".join(_WORD.findall((t or "").lower()))  # noqa: E731
    return norm(a) == norm(b)


def relevant(memories: Iterable[Memory], *, text: str, session_keys: set[str],
             exclude: set[str] = frozenset(), budget: int = 1500) -> list[Memory]:
    """What may matter for a turn about `text`, in sessions `session_keys`."""
    pool = [m for m in memories if m.id not in exclude]
    about_you = sorted((m for m in pool if m.about == "you"), key=lambda m: -m.created_ts)
    sessions = sorted((m for m in pool if m.about == "session" and m.session_key in session_keys),
                      key=lambda m: -m.created_ts)
    words = _words(text)
    scored = []
    for m in pool:
        if m.about == "general":
            shared = len(words & _words(m.text))
            if shared >= 2 or (shared == 1 and len(_words(m.text)) <= 6):
                scored.append((shared, m.created_ts, m))
    general = [m for _s, _t, m in sorted(scored, key=lambda x: (-x[0], -x[1]))[:6]]

    picked, used = [], 0
    for m in [*about_you[:12], *sessions[:12], *general]:
        if used + len(m.text) > budget:
            break
        picked.append(m)
        used += len(m.text) + 30
    return picked


def for_router(memories: list[Memory]) -> str:
    lines = [f"  [{m.id}] {ABOUT[m.about] + ': ' if ABOUT.get(m.about) else ''}{m.text}"
             for m in memories]
    return ("What you remember that may matter here (from earlier; forget(id) one that is wrong):\n"
            + "\n".join(lines))


def for_clone(memories: Iterable[Memory], session_key: str) -> str:
    """The person's preferences and this session's memories, for a clone's brief."""
    mine = [m for m in memories if m.about == "you"
            or (m.about == "session" and m.session_key == session_key)]
    if not mine:
        return ""
    mine.sort(key=lambda m: m.created_ts)
    return ("What Rimuru remembers that may help (from earlier sessions - follow it unless "
            "this job says otherwise):\n" + "\n".join(f"  - {m.text}" for m in mine[-20:]))


def groups(store: Any, title_of: Any) -> list[dict[str, Any]]:
    """Every memory, for the person: about them, per session, and general."""
    out: list[dict[str, Any]] = []
    mems = sorted(store.memories.values(), key=lambda m: -m.created_ts)
    you = [m.to_dict() for m in mems if m.about == "you"]
    out.append({"about": "you", "key": None, "title": "About you", "items": you})
    keys: dict[str, list[dict[str, Any]]] = {}
    for m in mems:
        if m.about == "session":
            keys.setdefault(m.session_key, []).append(m.to_dict())
    for key, items in keys.items():
        out.append({"about": "session", "key": key, "title": title_of(key), "items": items})
    out.append({"about": "general", "key": None, "title": "General",
                "items": [m.to_dict() for m in mems if m.about == "general"]})
    return out
