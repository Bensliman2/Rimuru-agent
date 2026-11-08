"""Sessions: separate conversations with Rimuru, each with its own clones and files.

A session is one conversation thread on a chat channel ("console"). Rimuru is
still one assistant across all of them - it remembers across sessions - but each
session's work is kept apart: its clones are listed only there, and they work in
the session's own folder, so two sessions never touch each other's files.

The person chooses that folder: any folder on their computer (a project they
already have), or - by default - a new one Rimuru makes in data/sessions/,
named after the session's first job. Either way it is fixed the first time a
clone works there, so renaming a session never moves its files. Work that does
not come from a chat session - practice runs, team channels - happens in the
practice folder (config.workspace, data/practice/).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .text import fold, name_words

DEFAULT = "direct"
# A session made before its first message (its folder was picked first).
UNNAMED = "New session"


def title_from(text: str, limit: int = 60) -> str:
    """A session's name, from the first thing the person asked."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text or UNNAMED
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(",.;:-") + "\u2026"


def is_session(thread: Any, config: Any) -> bool:
    return thread is not None and thread.channel in config.conversation_channels


# Never the last word of a name. Compared after fold().
_TAIL = {fold(w) for w in """a an the and or of for to in on with by at
le la les un une de du des et ou pour avec dans sur par au aux
في من إلى على مع و""".split()}


def _fit(words: list[str], limit: int) -> list[str]:
    """As many whole words as fit - or the start of the first one, if it is
    already too long (Chinese and Japanese are written without spaces)."""
    kept: list[str] = []
    for word in words:
        if len("-".join([*kept, word])) > limit:
            break
        kept.append(word)
    return kept or [word[:limit] for word in words[:1]]


def folder_name(thread: Any) -> str:
    """Readable and stable: the session's name, cut at a word, plus its own ID."""
    words = _fit(name_words(thread.title), 40)
    while len(words) > 1 and fold(words[-1]) in _TAIL:
        words.pop()                      # never end a name on a filler word
    slug = "-".join(words)
    tail = re.sub(r"[^a-z0-9]", "", thread.key.lower())[-6:] or "session"
    return f"{slug}-{tail}" if slug else tail


def folder_for(config: Any, thread: Any) -> Path:
    """Where a clone working for this thread keeps its files."""
    if not is_session(thread, config):
        return Path(config.workspace).resolve()
    chosen = getattr(thread, "folder", "") or getattr(thread, "rimuru_folder", "")
    if chosen:
        return Path(chosen)
    # Not fixed yet: the name it had before folders were stored (see claim_folder).
    return (Path(config.sessions_dir) / folder_name(thread)).resolve()


def folder_kind(thread: Any) -> str:
    """'picked' (the person's own folder), 'remote' (a folder on another machine,
    rimuru/hands), 'made' (Rimuru's), or 'new' (none yet)."""
    folder = str(getattr(thread, "folder", "") or "")
    if folder.startswith("hands://"):
        return "remote"
    if folder:
        return "picked"
    return "made" if getattr(thread, "rimuru_folder", "") else "new"


# Words that say what to do rather than what the thing is: a folder is called
# "browser-calculator", not "build-a-browser-calculator".
_LEAD = {fold(w) for w in """build make create write add fix please can could you u
i want need a an the me my some something help hi hey hello new simple small quick
créer crée faire fais écrire écris ajouter ajoute corriger corrige construire construis
je veux voudrais besoin un une le la les des du de mon ma mes nouveau nouvelle petit petite
أنشئ اصنع اكتب أضف اعمل أصلح ابن أريد من فضلك هل يمكنك لي جديد جديدة بسيط صغير""".split()}


def short_name(title: str, limit: int = 40) -> str:
    """A folder name from a job's title: 'Build a browser calculator with big
    buttons' -> 'browser-calculator-with-big-buttons'."""
    words = name_words(title)
    while len(words) > 1 and fold(words[0]) in _LEAD:
        words.pop(0)
    kept = _fit(words, limit)
    while len(kept) > 1 and fold(kept[-1]) in _TAIL:
        kept.pop()
    return "-".join(kept) or "session"


def claim_folder(config: Any, store: Any, thread: Any, hint: str = "") -> Path:
    """The session's folder, fixed the first time a clone works for it.

    A folder the person picked wins. Otherwise Rimuru makes one in
    data/sessions/, named after the first job (hint), and keeps it."""
    if not is_session(thread, config):
        return Path(config.workspace).resolve()
    if thread.folder or thread.rimuru_folder:
        return folder_for(config, thread)
    older = (Path(config.sessions_dir) / folder_name(thread)).resolve()
    if older.exists():                   # made before folders were stored: keep using it
        path = older
    else:
        base = Path(config.sessions_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        stem = short_name(hint or thread.title)
        path, n = base / stem, 1
        while True:                      # mkdir is the claim: two sessions never share one
            try:
                path.mkdir()
                break
            except FileExistsError:
                n += 1
                path = base / f"{stem}-{n}"
    thread.rimuru_folder = str(path)
    store.put_thread(thread)
    return path


def name_session(store: Any, thread: Any, job_title: str, task_id: str = "") -> None:
    """Name a session after its first job: "Build a browser calculator with big
    buttons" reads better in the list than "hi i want a calculator i can use in
    my br...". Later jobs leave the name alone."""
    title = " ".join(str(job_title or "").split())
    if title == "Write RIMURU.md: a guide to this project":      # /init (rimuru/commands.py)
        return
    if not title or any(t.thread_id == thread.id and t.id != task_id
                        for t in store.tasks.values()):
        return
    thread.title = title_from(title)
    store.put_thread(thread)


def display(path: Path) -> str:
    """A folder as the person can find it: from the project folder when inside it."""
    from .config import ROOT

    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def title_of(store: Any, thread: Any) -> str:
    if thread.title and thread.title != "direct line":
        return thread.title
    first = next((m["text"] for m in store.chat
                  if m["role"] == "user" and m.get("chat", DEFAULT) == thread.key), "")
    return title_from(first) if first else "Your first session"
