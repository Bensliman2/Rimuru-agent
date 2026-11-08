"""Skills (rimuru/skills.py): how-tos for every clone, one file each."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException

from ... import folders
from ... import sessions as chats
from ... import skills as skillbook
from .. import friendly
from ..shared import Web


def add_routes(app: FastAPI, web: Web) -> None:
    system = web.system

    def skills_listing() -> dict[str, Any]:
        folder = system.config.skills_dir
        every = skillbook.load_all(folder)
        used: dict[str, int] = {}             # how many clones read each one
        for worker in system.store.sessions_of_kind("worker"):
            for name in friendly._summary(system.store, worker)["read_skills"]:
                if (found := skillbook.match(every, name)) is not None:
                    used[found.slug] = used.get(found.slug, 0) + 1
        return {"items": [{**s.to_dict(), "used": used.get(s.slug, 0)} for s in every],
                "folder": chats.display(folder), "folder_abs": str(folder)}

    def skill_fields(payload: dict[str, Any]) -> dict[str, Any]:
        return {"name": str(payload.get("name") or ""), "when": str(payload.get("when") or ""),
                "text": str(payload.get("text") or ""), "enabled": bool(payload.get("enabled", True))}

    @app.get("/api/skills")
    async def list_skills() -> dict[str, Any]:
        return skills_listing()

    @app.post("/api/skills")
    async def add_skill(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            skill = skillbook.save(system.config.skills_dir, **skill_fields(payload))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from None
        system.bus.emit("skill.saved", skill=skill.slug, name=skill.name)
        return {"ok": True, "skill": skill.to_dict(), **skills_listing()}

    @app.put("/api/skills/{slug}")
    async def change_skill(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Change a skill - or only turn it on or off ({"enabled": false})."""
        folder = system.config.skills_dir
        try:
            if set(payload) == {"enabled"}:
                skill = skillbook.set_enabled(folder, slug, bool(payload["enabled"]))
            else:
                skill = skillbook.save(folder, slug=slug, **skill_fields(payload))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, "That skill is gone - its file was moved or deleted.") from None
        system.bus.emit("skill.saved", skill=skill.slug, name=skill.name)
        return {"ok": True, "skill": skill.to_dict(), **skills_listing()}

    @app.delete("/api/skills/{slug}")
    async def delete_skill(slug: str) -> dict[str, Any]:
        if not skillbook.delete(system.config.skills_dir, slug):
            raise HTTPException(404, "That skill is gone - its file was moved or deleted.")
        system.bus.emit("skill.deleted", skill=slug)
        return {"ok": True, **skills_listing()}

    @app.post("/api/skills/open-folder")
    async def open_skills_folder() -> dict[str, Any]:
        """Show the skills folder in File Explorer - always that folder, never a
        path from the request."""
        folder = system.config.skills_dir
        folder.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(folders.open_in_file_manager, folder)
        except Exception as exc:  # noqa: BLE001 - say so; the path can still be copied
            raise HTTPException(400, f"Could not open it: {exc}") from None
        return {"ok": True, "folder": str(folder)}
