"""Long-term memory: the person sees, adds, changes and deletes it all."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ... import memory as long_memory
from ... import sessions as chats
from ...ids import new_id
from ...models import Memory
from ..shared import Web, session_key


def add_routes(app: FastAPI, web: Web) -> None:
    system, session_thread = web.system, web.session_thread

    def memory_title(key: str) -> str:
        thread = session_thread(key)
        return chats.title_of(system.store, thread) if thread is not None else "A session"

    @app.get("/api/memories")
    async def memories() -> dict[str, Any]:
        return {"groups": long_memory.groups(system.store, memory_title),
                "count": len(system.store.memories)}

    def memory_text(payload: dict[str, Any]) -> str:
        text = " ".join(str(payload.get("text") or "").split())
        if not text:
            raise HTTPException(400, "Write what Rimuru should remember.")
        if len(text) > 500:
            raise HTTPException(400, "Keep it to one fact - 500 characters at most.")
        if long_memory.looks_secret(text):
            raise HTTPException(400, "That looks like a password or a key. Rimuru never keeps those.")
        return text

    @app.post("/api/memories")
    async def add_memory(payload: dict[str, Any]) -> dict[str, Any]:
        text = memory_text(payload)
        about = str(payload.get("about") or "you")
        key = ""
        if about == "session":
            key = session_key(payload.get("chat"))
        elif about not in {"you", "general"}:
            about = "you"
        mem = system.store.put_memory(Memory(id=new_id("memory"), text=text, about=about,
                                             session_key=key, source="you"))
        return {"ok": True, "memory": mem.to_dict()}

    @app.put("/api/memories/{memory_id}")
    async def edit_memory(memory_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        mem = system.store.memories.get(memory_id)
        if mem is None:
            raise HTTPException(404, "No such memory.")
        mem.text = memory_text(payload)
        mem.source = "you"
        system.store.put_memory(mem)
        return {"ok": True, "memory": mem.to_dict()}

    @app.delete("/api/memories/{memory_id}")
    async def delete_memory(memory_id: str, chat: str | None = None) -> dict[str, Any]:
        gone = system.store.delete_memory(memory_id)
        if gone is None:
            raise HTTPException(404, "No such memory.")
        if chat:
            # Forgotten from the chat: the chat says so, where it was remembered.
            system.store.add_chat("memory", gone.text, chat=session_key(chat), forgot=True)
        return {"ok": True}
