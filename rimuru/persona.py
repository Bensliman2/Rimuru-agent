"""Clones have names and personas: easier to follow ("Mika is on it"), and a
little character.

A persona sets a clone's VOICE - how it reports - never its scope: every clone
still builds the smallest thing that works (worker/session.py, QUICK_NOTE).
Names are short and original. A clone keeps its name for life - a restart
resumes the same clone, name and all - and two clones at work never share one.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

NAMES = [
    "Aiko", "Arlo", "Bram", "Cleo", "Dax", "Elio", "Esme", "Faye", "Gil", "Hana",
    "Ilka", "Ivo", "Juno", "Kai", "Kira", "Lev", "Lumi", "Mika", "Milo", "Nell",
    "Nico", "Noa", "Oren", "Pax", "Pia", "Quin", "Remy", "Rin", "Sage", "Sami",
    "Sora", "Tavi", "Teo", "Una", "Vale", "Wren", "Yara", "Yuki", "Zane", "Zia",
]

# key: (label, what the person reads, how the clone is told to sound)
PERSONAS: dict[str, tuple[str, str, str]] = {
    "steady": ("Steady", "works one careful step at a time",
               "You work one careful step at a time, and your reports say plainly what you checked."),
    "quick": ("Quick", "gets a first version working fast",
              "You like to get a working version out fast, and you end by saying what could come next."),
    "tidy": ("Tidy", "keeps things small and neat",
             "You keep things small and neat, and your reports are short."),
    "curious": ("Curious", "looks around before changing anything",
                "You look around before you change anything, and you mention anything surprising you notice."),
    "calm": ("Calm", "stays calm with tricky problems",
             "You stay calm with tricky problems, and you explain causes in simple words."),
    "practical": ("Practical", "picks the simplest thing that works",
                  "You pick the simplest thing that works, and you say why in one line."),
    "bright": ("Bright", "upbeat and encouraging",
               "You are upbeat and encouraging in your reports, without exaggerating anything."),
    "precise": ("Precise", "exact about what it changed",
                "You are exact: your reports name the files you changed and what each change does."),
}


def _number(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest(), 16)


def pick(session_id: str, taken: Iterable[str] = ()) -> tuple[str, str]:
    """A name no clone at work has, and a persona - (name, persona key)."""
    n = _number(session_id)
    busy = set(taken)
    names = [name for name in NAMES if name not in busy] or NAMES
    keys = list(PERSONAS)
    return names[n % len(names)], keys[(n // 97) % len(keys)]


def of(session: Any) -> tuple[str, str]:
    """A clone's name and persona key - the stored ones, or (for clones from
    before names) the same pick every time."""
    name, key = getattr(session, "name", ""), getattr(session, "persona", "")
    if name and key in PERSONAS:
        return name, key
    return pick(session.id)


def card(session: Any) -> dict[str, str]:
    name, key = of(session)
    label, line, _voice = PERSONAS[key]
    return {"name": name, "persona": label, "persona_line": line}


def introduction(session: Any) -> str:
    """The first line of a clone's brief."""
    name, key = of(session)
    return f"You are {name}, one of Rimuru's clones. {PERSONAS[key][2]}"
