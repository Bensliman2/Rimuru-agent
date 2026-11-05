"""Tool registry.

A Tool is a name, a JSON Schema and a handler. Handlers receive the parsed
arguments and the owning loop, so they can reach the session, the store and the
bus without globals.

The important distinction here is sync vs background. A background tool returns
"Started" immediately and delivers its real result later as an injected event
(article s4.1) - that is what stops a long operation from blocking the
conversation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..llm.base import ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from .loop import AgentLoop

Handler = Callable[[dict[str, Any], "AgentLoop"], Awaitable[str] | str]


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Handler
    background: bool = False

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, schema=self.schema)


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(
        self,
        name: str,
        description: str,
        schema: dict[str, Any],
        handler: Handler,
        *,
        background: bool = False,
    ) -> None:
        self.tools[name] = Tool(name, description, schema, handler, background)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools.values()]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def names(self) -> list[str]:
        return list(self.tools)


# --------------------------------------------------------------------------- #
# Schema helpers
# --------------------------------------------------------------------------- #
def obj(**props: Any) -> dict[str, Any]:
    required = [k for k, v in props.items() if not v.pop("_optional", False)]
    return {"type": "object", "properties": props, "required": required}


def s(desc: str, *, optional: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "string", "description": desc}
    if optional:
        d["_optional"] = True
    return d


def b(desc: str, *, optional: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "boolean", "description": desc}
    if optional:
        d["_optional"] = True
    return d


def enum(desc: str, values: list[str], *, optional: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "string", "description": desc, "enum": values}
    if optional:
        d["_optional"] = True
    return d


# --------------------------------------------------------------------------- #
# Tools every session gets: navigating the reference graph.
# --------------------------------------------------------------------------- #
def register_recall(registry: ToolRegistry) -> None:
    from ..recall import recall as do_recall

    async def handler(args: dict[str, Any], loop: "AgentLoop") -> str:
        obj_id = str(args.get("id", "")).strip()
        full = bool(args.get("full", False))
        loop.note_activity(f'recall({obj_id})')
        return do_recall(obj_id, loop.store, full=full, cap=loop.config.inline_content_cap)

    registry.add(
        "recall",
        "Look up any object in the system by its ID (trg_/thr_/wrk_/tsk_/job_). "
        "The result inlines the IDs of related objects, so you can follow "
        "references hop by hop to recover context you no longer hold. "
        "Pass full=true for the untruncated version.",
        obj(id=s("Object ID, e.g. thr_1a2b3c4d"), full=b("Return untruncated content")),
        handler,
    )
