"""The conversation: sessions, messages, commands, and stopping work."""
from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException

from ... import commands, persona
from ... import sessions as chats
from ... import skills as skillbook
from ..shared import Web, channel_of, session_key


def add_routes(app: FastAPI, web: Web) -> None:
    system, session_thread, last_said, folder_look, in_session = web.system, web.session_thread, web.last_said, web.folder_look, web.in_session

    @app.get("/api/chats")
    async def list_chats(limit: int = 60, also: str = "") -> dict[str, Any]:
        """The person's sessions, most recent first: the latest `limit` of them,
        and `also` - the one open now, however old it is."""
        store = system.store
        every = [t for t in store.threads.values() if chats.is_session(t, system.config)
                 and (store.chat_of(t.key) or store.tasks_of(t.id))]
        every.sort(key=last_said, reverse=True)
        items = []
        shown = every[:max(1, limit)]
        shown += [t for t in every[max(1, limit):] if t.key == also]
        for thread in shown:
            jobs = store.tasks_of(thread.id)
            working = sum(1 for t in jobs if t.status in {"running", "queued"})
            folder, shown, has_files, own_folder = folder_look(thread, len(jobs), working)
            items.append({
                "key": thread.key, "title": chats.title_of(store, thread),
                "created_ts": thread.created_ts,
                "last_ts": last_said(thread),
                "working": working,
                "jobs": len(jobs),
                "channel": thread.channel,
                "folder": shown if has_files else None,
                "folder_abs": str(folder),
                "own_folder": own_folder,
            })
        return {"items": items, "total": len(every)}

    @app.get("/api/chat")
    async def chat(chat: str = chats.DEFAULT) -> dict[str, Any]:
        store = system.store
        items: list[dict[str, Any]] = [dict(m) for m in store.chat_of(chat)]
        for item in items:
            if item.get("role") == "memory" and not item.get("forgot"):
                # Still kept? Then it can be forgotten from here.
                item["kept"] = item.get("memory_id") in store.memories
        # Show, inside the conversation, when Rimuru hands work to a clone and
        # when that work comes back - so the chat and the clones connect.
        thread = session_thread(chat)
        for task in store.tasks_of(thread.id if thread else None):
            clone = store.sessions.get(task.worker_id or "")
            look = persona.card(clone) if clone else {}
            base = {"role": "event", "task_id": task.id, "worker_id": task.worker_id,
                    "title": task.title, "clone": look.get("name", ""),
                    "color": look.get("color"), "edge": look.get("edge")}
            items.append({**base, "kind": "started", "ts": task.created_ts})
            if task.finished_ts and task.status in {"done", "failed", "cancelled"}:
                items.append({**base, "kind": task.status, "ts": task.finished_ts})
        items.sort(key=lambda m: m["ts"])

        # The typing dots mean "Rimuru is answering you": only while the person's
        # last message is unanswered - not while it is busy with a group chat.
        router = system.router
        busy = router.session.status in {"thinking", "tool"} or router.loop.inbox.qsize() > 0
        last_said = next((m for m in reversed(store.chat)
                          if m.get("chat", chats.DEFAULT) == chat and m["role"] in {"user", "rimuru"}),
                         None)
        age = time.time() - last_said["ts"] if last_said else 1e9
        unanswered = bool(last_said and last_said["role"] == "user" and age < 90)
        return {"messages": items, "thinking": unanswered and (busy or age < 10)}

    @app.post("/api/stop")
    async def stop_everything(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """The one button a non-technical person needs. In the chat it stops that
        session's work; under the hood (no session given) it stops everything."""
        key = str((payload or {}).get("chat") or "")
        live = [t for t in system.store.tasks.values() if t.status in {"running", "queued"}
                and (not key or in_session(t, key))]
        for task in live:
            await system.pool.stop(task, reason="stopped by the person")
        if not key and system.sim.running:
            system.sim.stop()
        if live:
            titles = "; ".join(f'"{t.title}"' for t in live)
            thread = session_thread(key) if key else None
            where = f'thread_id "{thread.id}"' if thread else 'the thread each task came from'
            # Rimuru must know, or it might pick the work back up on its own.
            system.router.loop.inject(
                f"The person pressed Stop. These tasks were cancelled: {titles}. "
                f"Do not restart them unless they ask. Acknowledge briefly in the "
                f"chat ({where}).",
                kind="human",
            )
        return {"ok": True, "stopped": len(live)}

    @app.post("/api/tasks/{task_id}/stop")
    async def stop_task(task_id: str) -> dict[str, Any]:
        task = system.store.tasks.get(task_id)
        if task is None:
            raise HTTPException(404, f"no task {task_id}")
        if task.status not in {"running", "queued"}:
            return {"ok": True, "stopped": 0}
        await system.pool.stop(task, reason="stopped by the person")
        system.router.loop.inject(
            f'The person stopped the task "{task.title}". Do not restart it unless '
            f'they ask. Acknowledge briefly in the chat (thread_id "{task.thread_id}").',
            kind="human",
        )
        return {"ok": True, "stopped": 1}

    @app.delete("/api/chats/{key}")
    async def delete_chat(key: str, files: bool = False) -> dict[str, Any]:
        """Delete a session for good - and, only when asked, Rimuru's own folder for it."""
        try:
            return await system.delete_session(session_key(key), delete_folder=files)
        except KeyError:
            raise HTTPException(404, "No such session.") from None

    @app.post("/api/message")
    async def message(payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty message")
        key = str(payload.get("thread_key") or "direct")
        # Typed in the app on a Telegram session: it stays that conversation, and
        # Rimuru's answer goes to Telegram as well. A command (/status) or a skill
        # forced for this message (/skill_web_pages ...) is handled on the way.
        return await system.person_says(text, channel=str(payload.get("channel") or channel_of(key)),
                                        thread_key=key)

    @app.get("/api/commands")
    async def list_commands() -> dict[str, Any]:
        """What can be typed after "/" in the chat: the commands, then one
        /skill_<name> for each skill - typed before a message, it makes that
        message's job use the skill."""
        usable = [s for s in skillbook.load_all(system.config.skills_dir) if s.enabled]
        return {"items": commands.COMMANDS + [
            {"name": skillbook.command_for(s), "about": f"Use your skill “{s.name}” for this message",
             "skill": True} for s in usable]}

    @app.post("/api/command")
    async def run_command(payload: dict[str, Any]) -> dict[str, Any]:
        """A command typed in a session's chat, like /init (rimuru/commands.py)."""
        key = session_key(payload.get("chat"))
        text = str(payload.get("text") or "").strip()
        if commands.parse(text) is None:
            raise HTTPException(400, "That is not a command. Commands look like /init.")
        return await system.run_command(key, text, channel=channel_of(key))
