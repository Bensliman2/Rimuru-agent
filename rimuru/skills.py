"""Skills: how-tos the person writes once, for every clone, in every project.

"Our Python style", "How we deploy", "Web pages that work on a phone". Each
skill is its own Markdown file in skills/ (Config.skills_dir) - easy to read,
edit by hand, copy or share:

    ---
    name: Our Python style
    when: Writing or changing Python code
    enabled: true
    ---
    - Type hints on every function.
    - A pytest test for every bug fix.

How they reach the clones, cheaply: each clone's brief lists only the skills'
names and when to use them; a clone reads one in full with use_skill() when it
fits its job (worker/session.py). Twenty skills cost a few lines per clone,
not twenty pages.

Different from the project guide (RIMURU.md: facts about one project, from
/init) and from memory (short facts about the person): a skill is a way of
working, for any project. Skills are the person's own instructions - text, not
code: they give a clone no new powers.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .memory import looks_secret

MAX_NAME = 60
MAX_WHEN = 200
MAX_TEXT = 20000
MAX_LISTED = 30
MAX_FILE = 200_000

_SLUG = re.compile(r"[a-z0-9]+")


@dataclass
class Skill:
    slug: str                 # its file: skills/<slug>.md - kept when the skill is renamed
    name: str
    when: str
    text: str
    enabled: bool = True
    updated_ts: float = 0.0

    @property
    def file(self) -> str:
        return f"{self.slug}.md"

    def to_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "name": self.name, "when": self.when, "text": self.text,
                "enabled": self.enabled, "updated_ts": self.updated_ts, "file": self.file}


def slugify(name: str) -> str:
    """'Our Python style!' -> 'our-python-style'. Only a-z, 0-9 and '-', so a
    slug can never point outside the skills folder."""
    words = _SLUG.findall(str(name or "").lower())
    slug = ""
    for word in words:
        if len(slug) + len(word) + 1 > 48:
            break
        slug = f"{slug}-{word}" if slug else word
    return slug or "skill"


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def parse(slug: str, raw: str, ts: float = 0.0) -> Skill:
    """A skill file, written by Rimuru or by hand. Without its header the whole
    file is the instructions and the name comes from the file's name."""
    meta: dict[str, str] = {}
    body = raw.replace("\r\n", "\n")
    if body.startswith("---\n"):
        head, sep, rest = body[4:].partition("\n---")
        if sep:
            for line in head.splitlines():
                key, colon, value = line.partition(":")
                if colon:
                    meta[key.strip().lower()] = value.strip()
            body = rest[1:] if rest.startswith("\n") else rest
    enabled = meta.get("enabled", "true").lower() not in {"false", "no", "off", "0"}
    name = _clean(meta.get("name"), MAX_NAME) or slug.replace("-", " ").capitalize()
    return Skill(slug=slug, name=name, when=_clean(meta.get("when"), MAX_WHEN),
                 text=body.strip(), enabled=enabled, updated_ts=ts)


def render(skill: Skill) -> str:
    return (f"---\nname: {skill.name}\nwhen: {skill.when}\n"
            f"enabled: {'true' if skill.enabled else 'false'}\n---\n{skill.text.strip()}\n")


def load_all(folder: Path) -> list[Skill]:
    """Every skill in the folder, by name - including files made by hand, with any
    name ("Deploy steps.md"). A file that cannot be read is skipped."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = []
    for path in folder.glob("*.md"):
        if path.name.lower() == "readme.md" or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_FILE:
                continue
            out.append(parse(path.stem, path.read_text(encoding="utf-8"), path.stat().st_mtime))
        except (OSError, UnicodeDecodeError):
            continue
    return sorted(out, key=lambda s: s.name.lower())


def match(skills: Iterable[Skill], key: str) -> Skill | None:
    """The skill a clone or a person means: by its file name (slug) or its name,
    any case."""
    key = _clean(key, 200).lower().removesuffix(".md")
    if not key:
        return None
    every = list(skills)
    return (next((s for s in every if s.slug.lower() == key), None)
            or next((s for s in every if s.name.lower() == key), None)
            or next((s for s in every if slugify(s.slug) == slugify(key)), None))


def get(folder: Path, key: str) -> Skill | None:
    return match(load_all(folder), key)


def problem(name: str, when: str, text: str) -> str | None:
    """What is wrong with a skill, in plain words - or None."""
    if not _clean(name, 1000):
        return "Give the skill a name."
    if len(_clean(name, 1000)) > MAX_NAME:
        return f"Keep the name under {MAX_NAME} characters."
    if len(_clean(when, 10_000)) > MAX_WHEN:
        return f"Keep \"when to use it\" under {MAX_WHEN} characters."
    if not str(text or "").strip():
        return "Write what the clones should do."
    if len(str(text)) > MAX_TEXT:
        return f"That is too long for one skill (over {MAX_TEXT // 1000}k characters). Split it in two."
    if any(looks_secret(part) for part in (name, when, text)):
        return ("That looks like a password or a key. Skills are plain files that every clone "
                "reads, so keep secrets out - API keys belong in Models or Connections.")
    return None


def _write(path: Path, content: str) -> None:
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def save(folder: Path, *, name: str, when: str = "", text: str, enabled: bool = True,
         slug: str | None = None) -> Skill:
    """Create a skill (slug None) or change one. Its file keeps its name when the
    skill is renamed. Raises ValueError with a plain reason, FileExistsError or
    FileNotFoundError."""
    issue = problem(name, when, text)
    if issue:
        raise ValueError(issue)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if slug is None:
        slug = slugify(name)
        if (folder / f"{slug}.md").exists():
            raise FileExistsError(f"There is already a skill called \"{name}\" ({slug}.md).")
    elif not _known(folder, slug):
        raise FileNotFoundError("No such skill.")
    skill = Skill(slug=slug, name=_clean(name, MAX_NAME), when=_clean(when, MAX_WHEN),
                  text=str(text).strip(), enabled=bool(enabled), updated_ts=time.time())
    _write(folder / skill.file, render(skill))
    return skill


def _known(folder: Path, slug: str) -> bool:
    """Only a skill file that is really there - a request's slug is never turned
    into a path before it matches one, so it cannot reach outside the folder."""
    return any(s.slug == slug for s in load_all(folder))


def set_enabled(folder: Path, slug: str, enabled: bool) -> Skill:
    skill = next((s for s in load_all(folder) if s.slug == slug), None)
    if skill is None:
        raise FileNotFoundError("No such skill.")
    return save(folder, name=skill.name, when=skill.when, text=skill.text, enabled=enabled, slug=slug)


def delete(folder: Path, slug: str) -> bool:
    if not _known(folder, slug):
        return False
    (Path(folder) / f"{slug}.md").unlink()
    return True


# ---------------------------------------------------------------------- #
# what the clones and Rimuru see
# ---------------------------------------------------------------------- #
def for_brief(skills: Iterable[Skill]) -> str:
    """The short list in a clone's brief: names and when - never the whole text."""
    usable = [s for s in skills if s.enabled][:MAX_LISTED]
    if not usable:
        return ""
    lines = [f"- {s.slug}: {s.name}" + (f" - use when: {s.when}" if s.when else "") for s in usable]
    return ("SKILLS - how-tos the person wrote for helpers like you. When one fits your job, read it "
            "in full with use_skill(name) before you start that part, and follow it unless the job "
            "says otherwise. Don't read the ones that don't fit.\n" + "\n".join(lines) + "\n\n")


def for_router(skills: Iterable[Skill]) -> str:
    usable = [s for s in skills if s.enabled]
    if not usable:
        return "The person has no skills set up yet (they add them with the Skills button)."
    return ("Skills the person wrote for your clones (each clone sees this list and reads the ones "
            "that fit its job; you can name one in a brief): "
            + "; ".join(f"{s.name}" + (f" (when: {s.when})" if s.when else "") for s in usable) + ".")


def used_names(steps: Iterable[Any]) -> list[str]:
    """The skills a clone read, in order, from its steps' tool calls."""
    seen: list[str] = []
    for step in steps:
        for call in getattr(step, "tool_calls", None) or []:
            if call.get("name") == "use_skill":
                name = _clean((call.get("args") or {}).get("name"), 80)
                if name and name not in seen:
                    seen.append(name)
    return seen
