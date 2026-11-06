"""Prefixed object IDs.

Chain of Recall (article s3.3) needs every object in the system to carry a stable,
readable, *typeable* identity, because the model navigates the reference graph by
emitting these strings back to us in a recall() tool call.
"""
from __future__ import annotations

import secrets

PREFIXES = {
    "trigger": "trg",
    "thread": "thr",
    "worker": "wrk",
    "task": "tsk",
    "router": "rtr",
    "watchdog": "wdg",
    "step": "stp",
    "job": "job",
    "memory": "mem",
}

KIND_BY_PREFIX = {v: k for k, v in PREFIXES.items()}

ROUTER_SESSION_ID = "rtr_main"
WATCHDOG_SESSION_ID = "wdg_main"


def new_id(kind: str) -> str:
    prefix = PREFIXES.get(kind)
    if prefix is None:
        raise ValueError(f"unknown object kind: {kind}")
    return f"{prefix}_{secrets.token_hex(4)}"


def kind_of(obj_id: str) -> str | None:
    if not obj_id or "_" not in obj_id:
        return None
    return KIND_BY_PREFIX.get(obj_id.split("_", 1)[0])
