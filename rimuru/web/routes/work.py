"""The clones: what each is doing and has done."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ... import sessions as chats
from ...hands import remote
from ...models import TERMINAL
from .. import friendly
from ..shared import Web


def add_routes(app: FastAPI, web: Web) -> None:
    system, session_thread, folder_info, task_folder = web.system, web.session_thread, web.folder_info, web.task_folder

    @app.get("/api/work")
    async def work(chat: str | None = None, limit: int = 60) -> dict[str, Any]:
        """Clones, each with the session it works for: those at work and the latest
        `limit` - or, with ?chat=, all of that session's, plus where it works."""
        conv = system.config.conversation_channels
        store = system.store
        cards = []
        workers = store.sessions_of_kind("worker")
        total, finished = len(workers), sum(s.status == "done" for s in workers)
        if chat is not None:
            thread = session_thread(chat)
            wanted = {t.worker_id for t in store.tasks_of(thread.id if thread else None)}
            picked = [s for s in workers if s.id in wanted]
        else:
            newest = sorted(workers, key=lambda s: s.created_ts, reverse=True)
            picked = [s for i, s in enumerate(newest) if i < limit or s.status not in TERMINAL]
        for s in picked:
            task = store.tasks.get(s.task_id or "")
            card = friendly.worker_card(store, s, conv)
            thread = store.threads.get((task.thread_id if task else None) or "")
            card["session"] = ({"key": thread.key, "title": chats.title_of(store, thread)}
                               if chats.is_session(thread, system.config) else None)
            cards.append(card)
        cards.sort(key=lambda c: c["started_ts"] or 0, reverse=True)
        overview = system.overview()
        return {
            "cards": cards,
            "total": total,
            "done": finished,
            "folder": folder_info(chat) if chat is not None else None,
            "demo": overview["demo"],
            "demo_forced": system.config.demo_forced(),
            "setup_needed": system.setup_needed(),
            "view_port": app.state.view_port,
            "save_problem": system.store.save_problem,
            "blocked": overview["blocked"],
            "quota": overview["quota"],
            "provider": overview["models"]["worker"]["provider"],
            "model": overview["models"]["worker"]["model"],
            "shell": overview["shell"],
            "sim_running": overview["sim"]["running"],
        }

    @app.get("/api/work/{worker_id}")
    async def work_detail(worker_id: str) -> dict[str, Any]:
        session = system.store.sessions.get(worker_id)
        if session is None or session.kind != "worker":
            raise HTTPException(404, f"no clone {worker_id}")
        task = system.store.tasks.get(session.task_id or "")
        far = remote.parse(task.folder) if task is not None else None
        if far is not None:
            # On another machine: named there, as "laptop:C:\work\site".
            shown = f"{system.hands.name(far[0])}:{far[1]}"
            where = {"folder": shown, "folder_abs": far[1]}
        else:
            folder = task_folder(task)
            where = {"folder": chats.display(folder), "folder_abs": str(folder)}
        return {
            **where,
            "card": friendly.worker_card(system.store, session, system.config.conversation_channels),
            "asked": friendly.plain(task.brief) if task else "",
            "timeline": friendly.worker_timeline(system.store, session),
            "stages": friendly.worker_stages(system.store, session),
            "model": session.model,
        }
