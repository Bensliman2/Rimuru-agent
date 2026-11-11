"""Where a session works: folders on this computer and on other machines."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import quote
from typing import Any

from fastapi import FastAPI, HTTPException, Response

from ... import folders
from ... import sessions as chats
from ...hands import remote
from ...hands.hub import RemoteError as HandsRemoteError, Unavailable as HandsUnavailable
from ..shared import Web, channel_of, session_key
from ..viewer import zip_folder


def add_routes(app: FastAPI, web: Web) -> None:
    system, session_thread, busy_in, folder_info = web.system, web.session_thread, web.busy_in, web.folder_info

    @app.post("/api/session/folder")
    async def set_session_folder(payload: dict[str, Any]) -> dict[str, Any]:
        """Choose where a session works: a folder of the person's (path), or - with
        no path - a new folder Rimuru makes for it."""
        key = session_key(payload.get("chat"))
        store = system.store
        thread = session_thread(key) or store.get_or_create_thread(channel_of(key), key, chats.UNNAMED)
        if busy_in(thread):
            raise HTTPException(409, "This session's clones are still working. Change its "
                                     "folder when they finish, or stop them first.")
        raw = str(payload.get("path") or "").strip()
        machine_id = str(payload.get("machine") or "").strip()
        if machine_id and raw:
            # A folder on another machine: it checks the folder is one it shares.
            try:
                found = await system.hands.request(machine_id, "dirs", path=raw, timeout=20)
            except (HandsUnavailable, HandsRemoteError) as exc:
                raise HTTPException(400, str(exc)) from None
            thread.folder = remote.ref(machine_id, str(found["path"]))
        elif raw:
            try:
                thread.folder = str(folders.check(raw, system.config))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        else:
            thread.folder = ""
        store.put_thread(thread)
        return {"ok": True, "folder": folder_info(key)}

    @app.get("/api/hands")
    async def hands_status() -> dict[str, Any]:
        return system.hands.status()

    @app.post("/api/hands/pair")
    async def hands_pair() -> dict[str, Any]:
        """A one-time code, and the command to run on the other machine."""
        await system.hands.listen()
        code = system.hands.new_code()
        return {**system.hands.status(), "code": code, "command": system.hands.command(code),
                "expires_in": 600}

    @app.post("/api/hands/dirs")
    async def hands_dirs(payload: dict[str, Any]) -> dict[str, Any]:
        """The folders on another machine, to pick a session's folder from."""
        try:
            return await system.hands.request(str(payload.get("machine") or ""), "dirs",
                                              path=str(payload.get("path") or ""), timeout=20)
        except (HandsUnavailable, HandsRemoteError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.delete("/api/hands/{machine_id}")
    async def hands_remove(machine_id: str) -> dict[str, Any]:
        if machine_id not in system.hands.machines:
            raise HTTPException(404, "No such machine.")
        await system.hands.remove(machine_id)
        return system.hands.status()

    @app.post("/api/folders")
    async def list_folders(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """The folders inside one folder, to pick from. POST, so that only this
        app's own page can ask (the Origin check in local_only)."""
        return await asyncio.to_thread(folders.listing, (payload or {}).get("path"), system.config)

    @app.post("/api/folders/new")
    async def new_folder(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            path = folders.make(payload.get("parent", ""), payload.get("name", ""), system.config)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True, "path": str(path)}

    @app.get("/api/session/zip")
    async def session_zip(chat: str) -> Response:
        """The session's files as one zip - never the files that hold keys."""
        info = folder_info(session_key(chat))
        if info["kind"] in {"new", "remote"} or not info["exists"]:
            raise HTTPException(404, "This session has no folder of files here yet.")
        try:
            data = await asyncio.to_thread(zip_folder, Path(info["path"]))
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from None
        name = re.sub(r"[^\w.-]+", "-", info["name"]).strip("-") or "files"
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"{name.encode('ascii', 'ignore').decode() or 'files'}.zip\"; "
                                   f"filename*=UTF-8''{quote(name)}.zip"})

    @app.post("/api/session/open-folder")
    async def open_session_folder(payload: dict[str, Any]) -> dict[str, Any]:
        """Open the session's folder in File Explorer. Only that folder: the path
        is the session's, never one taken from the request."""
        info = folder_info(session_key(payload.get("chat")))
        if info["kind"] == "remote":
            raise HTTPException(400, f"That folder is on {info['machine']} - open it there.")
        if info["kind"] == "new":
            raise HTTPException(404, "This session has no folder yet. Rimuru makes one when "
                                     "its first job starts.")
        if not info["exists"]:
            raise HTTPException(404, "That folder is not there any more.")
        try:
            await asyncio.to_thread(folders.open_in_file_manager, Path(info["path"]))
        except Exception as exc:  # noqa: BLE001 - say so; the path can still be copied
            raise HTTPException(500, f"Could not open it here ({type(exc).__name__}). "
                                     "Copy the path instead.") from None
        return {"ok": True}
