"""FastAPI surface for the dashboard.

Everything the UI shows is read out of the Store, and every state change the
engine makes is mirrored onto a WebSocket, so the dashboard is a live view of
the running system rather than a periodic poll of a summary.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import CONFIG, ROOT
from ..llm import catalog
from ..recall import recall as do_recall
from .. import sessions as chats
from ..system import RimuruSystem
from . import friendly

STATIC = Path(__file__).parent / "static"

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


class _NoTask:
    thread_id = None


_NO_TASK = _NoTask()


def _host_of(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.split(":", 1)[0]


def create_app(*, speed: float = 4.0, autostart: str | None = None) -> FastAPI:
    system = RimuruSystem(CONFIG, speed=speed)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await system.start()
        if autostart:
            await asyncio.sleep(0.5)
            system.start_scenario(autostart)
        yield
        with contextlib.suppress(Exception):
            await system.stop()

    app = FastAPI(title="Rimuru Super-agent", docs_url="/api/docs", lifespan=lifespan)
    app.state.system = system

    # The app stores API keys and can point models at any server, so it answers
    # only this computer: no other website may drive it (cross-site requests),
    # and no other hostname may reach it (DNS rebinding). Serving on a network
    # address (RIMURU_HOST=0.0.0.0) is a deliberate choice; RIMURU_ALLOWED_HOSTS
    # lists extra names to accept.
    allowed_hosts = LOCAL_HOSTS | {CONFIG.host.lower()} | {
        h.strip().lower() for h in os.environ.get("RIMURU_ALLOWED_HOSTS", "").split(",") if h.strip()}
    open_host = CONFIG.host in {"0.0.0.0", "::"}

    def _foreign(headers, method: str) -> str | None:
        host = headers.get("host", "")
        if not open_host and _host_of(host) not in allowed_hosts:
            return "This app only answers on this computer."
        origin = headers.get("origin")
        if origin and method not in {"GET", "HEAD", "OPTIONS"} and \
                urlparse(origin).netloc.lower() != host.lower():
            return "Requests from other websites are refused."
        return None

    @app.middleware("http")
    async def local_only(request, call_next):
        refused = _foreign(request.headers, request.method)
        if refused:
            return JSONResponse({"error": refused}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def revalidate_assets(request, call_next):
        # Without this, browsers heuristically cache the dashboard and keep
        # showing an old UI after an update. no-cache still allows caching - it
        # just revalidates against the ETag, which costs one 304.
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # ------------------------------------------------------------------ #
    # pages
    # ------------------------------------------------------------------ #
    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        """The chat app, for people who do not use agents."""
        return FileResponse(STATIC / "index.html")

    @app.get("/inside")
    async def inside() -> FileResponse:
        """The technical dashboard: every session, tool call and decision."""
        return FileResponse(STATIC / "inside.html")

    # ------------------------------------------------------------------ #
    # the chat app's read models
    # ------------------------------------------------------------------ #
    def session_thread(key: str):
        return system.store.threads.get(system.store.thread_by_key.get(f"console:{key}", ""))

    def in_session(task, key: str) -> bool:
        thread = system.store.threads.get(task.thread_id or "")
        return thread is not None and thread.channel == "console" and thread.key == key

    @app.get("/api/chats")
    async def list_chats() -> dict[str, Any]:
        """The person's sessions, most recent first."""
        store = system.store
        items = []
        for thread in store.threads.values():
            if not chats.is_session(thread, system.config):
                continue
            said = [m for m in store.chat if m.get("chat", chats.DEFAULT) == thread.key]
            jobs = [t for t in store.tasks.values() if t.thread_id == thread.id]
            if not said and not jobs:
                continue
            folder = chats.folder_for(system.config, thread)
            try:
                shown = str(folder.relative_to(ROOT)).replace("\\", "/")
            except ValueError:
                shown = str(folder)
            items.append({
                "key": thread.key, "title": chats.title_of(store, thread),
                "created_ts": thread.created_ts,
                "last_ts": max([thread.last_ts] + [m["ts"] for m in said]),
                "working": sum(1 for t in jobs if t.status in {"running", "queued"}),
                "jobs": len(jobs),
                "folder": shown if folder.exists() and any(folder.iterdir()) else None,
            })
        items.sort(key=lambda c: c["last_ts"], reverse=True)
        return {"items": items}

    @app.get("/api/chat")
    async def chat(chat: str = chats.DEFAULT) -> dict[str, Any]:
        store = system.store
        items: list[dict[str, Any]] = [dict(m) for m in store.chat
                                       if m.get("chat", chats.DEFAULT) == chat]
        # Show, inside the conversation, when Rimuru hands work to a clone and
        # when that work comes back - so the chat and the clones connect.
        for task in store.tasks.values():
            if not in_session(task, chat):
                continue
            base = {"role": "event", "task_id": task.id, "worker_id": task.worker_id,
                    "title": task.title}
            items.append({**base, "kind": "started", "ts": task.created_ts})
            if task.finished_ts and task.status in {"done", "failed", "cancelled"}:
                items.append({**base, "kind": task.status, "ts": task.finished_ts})
        items.sort(key=lambda m: m["ts"])

        # The typing dots mean "Rimuru is answering you": only while the person's
        # last message is unanswered - not while it is busy with a group chat.
        router = system.router
        busy = router.session.status in {"thinking", "tool"} or router.loop.inbox.qsize() > 0
        last_said = next((m for m in reversed(store.chat)
                          if m.get("chat", chats.DEFAULT) == chat), None)
        age = time.time() - last_said["ts"] if last_said else 1e9
        unanswered = bool(last_said and last_said["role"] == "user" and age < 90)
        return {"messages": items, "thinking": unanswered and (busy or age < 10)}

    @app.get("/api/work")
    async def work(chat: str | None = None) -> dict[str, Any]:
        """Every clone - or, with ?chat=, only that session's."""
        conv = system.config.conversation_channels
        store = system.store
        cards = [
            friendly.worker_card(store, s, conv)
            for s in store.sessions_of_kind("worker")
            if chat is None or in_session(store.tasks.get(s.task_id or "") or _NO_TASK, chat)
        ]
        cards.sort(key=lambda c: c["started_ts"] or 0, reverse=True)
        overview = system.overview()
        return {
            "cards": cards,
            "demo": overview["demo"],
            "demo_forced": system.config.demo_forced(),
            "blocked": overview["blocked"],
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
        return {
            "card": friendly.worker_card(system.store, session, system.config.conversation_channels),
            "asked": friendly.plain(task.brief) if task else "",
            "timeline": friendly.worker_timeline(system.store, session),
            "stages": friendly.worker_stages(system.store, session),
            "model": session.model,
        }

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

    # ------------------------------------------------------------------ #
    # live feed
    # ------------------------------------------------------------------ #
    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        # Browsers let any page open a WebSocket, so check who is asking.
        if _foreign(socket.headers, "WS"):
            await socket.close(code=1008)
            return
        await socket.accept()
        queue = system.bus.subscribe_queue()
        try:
            # Prime the client with recent history so a late join is not blank.
            await socket.send_json({"topic": "_backlog", "events": system.bus.recent(120)})
            while True:
                event = await queue.get()
                await socket.send_json(event)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            system.bus.unsubscribe_queue(queue)

    # ------------------------------------------------------------------ #
    # read models
    # ------------------------------------------------------------------ #
    @app.get("/api/overview")
    async def overview() -> dict[str, Any]:
        return system.overview()

    @app.get("/api/triggers")
    async def triggers(limit: int = 120, verdict: str | None = None) -> dict[str, Any]:
        items = [t.to_dict() for t in system.store.recent_triggers(limit=400)]
        if verdict:
            items = [t for t in items if t["verdict"] == verdict]
        return {"items": items[:limit]}

    @app.get("/api/threads")
    async def threads() -> dict[str, Any]:
        items = sorted(
            (t.to_dict() for t in system.store.threads.values()),
            key=lambda t: t["last_ts"], reverse=True,
        )
        return {"items": items}

    @app.get("/api/tasks")
    async def tasks() -> dict[str, Any]:
        return {"items": [t.to_dict() for t in system.store.all_tasks()]}

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str) -> dict[str, Any]:
        task = system.store.tasks.get(task_id)
        if task is None:
            raise HTTPException(404, f"no task {task_id}")
        payload: dict[str, Any] = {"task": task.to_dict()}
        if task.worker_id:
            payload["worker"] = (
                system.store.sessions[task.worker_id].to_dict()
                if task.worker_id in system.store.sessions else None
            )
            payload["transcript"] = [
                s.to_dict() for s in system.store.transcript(task.worker_id)
            ]
        if task.thread_id and task.thread_id in system.store.threads:
            payload["thread"] = system.store.threads[task.thread_id].to_dict()
        payload["jobs"] = [
            j.to_dict() for j in system.store.jobs.values()
            if j.session_id == task.worker_id
        ]
        return payload

    @app.get("/api/sessions")
    async def sessions() -> dict[str, Any]:
        items = sorted(
            (s.to_dict() for s in system.store.sessions.values()),
            key=lambda s: (s["kind"] != "router", s["kind"] != "watchdog", -s["created_ts"]),
        )
        return {"items": items}

    @app.get("/api/sessions/{session_id}")
    async def session_detail(session_id: str) -> dict[str, Any]:
        session = system.store.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, f"no session {session_id}")
        worker = system.pool.get(session_id)
        return {
            "session": session.to_dict(),
            "transcript": [s.to_dict() for s in system.store.transcript(session_id)],
            "task": (
                system.store.tasks[session.task_id].to_dict()
                if session.task_id and session.task_id in system.store.tasks else None
            ),
            "jobs": [
                j.to_dict() for j in system.store.jobs.values()
                if j.session_id == session_id
            ],
            "queued": worker.loop.inbox.qsize() if worker else (
                system.router.loop.inbox.qsize() if session_id == "rtr_main" else 0
            ),
            "speculative": bool(worker.speculative) if worker else False,
        }

    @app.get("/api/jobs")
    async def jobs() -> dict[str, Any]:
        items = sorted(
            (j.to_dict() for j in system.store.jobs.values()),
            key=lambda j: j["started_ts"], reverse=True,
        )
        return {"items": items[:120]}

    @app.get("/api/outbox")
    async def outbox(limit: int = 80) -> dict[str, Any]:
        return {"items": system.channels.recent(limit)}

    @app.get("/api/events")
    async def events(limit: int = 300) -> dict[str, Any]:
        return {"items": system.bus.recent(limit)}

    @app.get("/api/graph")
    async def graph() -> dict[str, Any]:
        return system.graph()

    @app.get("/api/recall/{obj_id}")
    async def recall_object(obj_id: str, full: bool = False) -> dict[str, Any]:
        """The Chain of Recall explorer: exactly what the model would see."""
        return {
            "id": obj_id,
            "text": do_recall(obj_id, system.store, full=full,
                              cap=system.config.inline_content_cap),
        }

    @app.get("/api/search")
    async def search(q: str = "", limit: int = 40) -> dict[str, Any]:
        """Find anything Rimuru knows by a few words - no IDs needed. With no
        words, the most recent sessions, jobs and notes."""
        store = system.store
        words = [w for w in q.lower().split() if w]
        found = lambda *texts: all(w in " ".join(str(t or "") for t in texts).lower() for w in words)  # noqa: E731
        label = {"console": "Your chat", "im": "Team chat", "github": "GitHub"}
        status = {"running": "working", "queued": "waiting", "done": "done", "failed": "couldn't finish",
                  "cancelled": "stopped"}
        items: list[dict[str, Any]] = []
        for thread in store.threads.values():
            if chats.is_session(thread, system.config):
                title = chats.title_of(store, thread)
                if found(title):
                    items.append({"kind": "session", "id": thread.id, "key": thread.key, "title": title,
                                  "sub": f"Session \u00b7 {len(thread.trigger_ids)} message"
                                         + ("" if len(thread.trigger_ids) == 1 else "s"),
                                  "ts": thread.last_ts})
            elif words and found(thread.title):
                items.append({"kind": "conversation", "id": thread.id, "title": thread.title,
                              "sub": f"{label.get(thread.channel, thread.channel)} conversation", "ts": thread.last_ts})
        for task in store.tasks.values():
            if found(task.title, task.brief, task.result):
                items.append({"kind": "job", "id": task.id, "title": task.title, "clone": task.worker_id,
                              "sub": f"Job \u00b7 {status.get(task.status, task.status)}", "ts": task.created_ts})
        if words:
            for trigger in store.triggers.values():
                if found(trigger.content, trigger.author):
                    items.append({"kind": "message", "id": trigger.id,
                                  "title": " ".join(trigger.content.split())[:120],
                                  "sub": f"Message from {trigger.author} \u00b7 {label.get(trigger.channel, trigger.channel)}",
                                  "ts": trigger.ts})
        for note in store.notes:
            if found(note["note"]):
                items.append({"kind": "note", "id": None, "title": note["note"],
                              "sub": "Something Rimuru chose to remember", "ts": note["ts"]})
        items.sort(key=lambda i: i["ts"], reverse=True)
        return {"items": items[:limit], "total": len(items)}

    @app.get("/api/batching")
    async def batching(window: int = 8) -> dict[str, Any]:
        """Router batch size per turn and amortized time per decision (s7.2).

        A decision can span several LLM requests - the turn that decides, then
        the turn that reads the tool results - so amortized time is total router
        inference time over a rolling window divided by the events decided in
        it, not one turn's latency divided by its own batch.
        """
        steps = system.store.transcript("rtr_main")
        items = []
        for i, s in enumerate(steps):
            span = steps[max(0, i - window + 1): i + 1]
            decided = sum(x.batch_size for x in span)
            spent = sum(x.latency_ms for x in span)
            items.append({
                "index": s.index, "ts": s.ts, "batch_size": s.batch_size,
                "latency_ms": round(s.latency_ms, 1),
                "amortized_ms": round(spent / decided, 1) if decided else None,
                "tool_calls": len(s.tool_calls),
            })
        return {"items": items}

    # ------------------------------------------------------------------ #
    # controls
    # ------------------------------------------------------------------ #
    @app.post("/api/sim/start")
    async def sim_start(payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("scenario") or "team")
        speed_value = payload.get("speed")
        kwargs: dict[str, Any] = {}
        if payload.get("total_events"):
            kwargs["total_events"] = int(payload["total_events"])
        if payload.get("work_requests"):
            kwargs["work_requests"] = int(payload["work_requests"])
        try:
            scenario = system.start_scenario(
                name,
                speed=float(speed_value) if speed_value else None,
                **kwargs,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "scenario": scenario.summary()}

    @app.post("/api/sim/stop")
    async def sim_stop() -> dict[str, Any]:
        system.sim.stop()
        return {"ok": True}

    @app.post("/api/sim/pause")
    async def sim_pause() -> dict[str, Any]:
        system.sim.pause()
        return {"ok": True}

    @app.post("/api/sim/resume")
    async def sim_resume() -> dict[str, Any]:
        system.sim.resume()
        return {"ok": True}

    @app.post("/api/message")
    async def message(payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty message")
        trigger = await system.human_message(
            text,
            channel=str(payload.get("channel") or "console"),
            thread_key=str(payload.get("thread_key") or "direct"),
        )
        return {"ok": True, "trigger_id": trigger.id}

    @app.post("/api/watchdog/ask")
    async def watchdog_ask(payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question") or "").strip()
        if not question:
            raise HTTPException(400, "empty question")
        system.watchdog.ask(question)
        return {"ok": True}

    @app.get("/api/watchdog/health")
    async def watchdog_health() -> dict[str, Any]:
        return system.watchdog.health_report()

    # ------------------------------------------------------------------ #
    # policies (the natural-language extension point of s3.1)
    # ------------------------------------------------------------------ #
    @app.get("/api/policies")
    async def get_policies() -> dict[str, Any]:
        return system.filter.describe()

    @app.post("/api/policies/semantic")
    async def set_semantic(payload: dict[str, Any]) -> dict[str, Any]:
        policy_id = str(payload.get("id") or "")
        if policy_id:
            updated = system.filter.set_semantic_policy(
                policy_id,
                enabled=payload.get("enabled"),
                text=payload.get("text"),
            )
            if updated is None:
                raise HTTPException(404, f"no policy {policy_id}")
            return {"ok": True, "policy": updated}
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "a new policy needs text")
        return {"ok": True, "policy": system.filter.add_semantic_policy(text)}

    @app.post("/api/policies/channel")
    async def set_channel(payload: dict[str, Any]) -> dict[str, Any]:
        channel = str(payload.get("channel") or "").strip()
        mode = str(payload.get("mode") or "").strip()
        if not channel or mode not in {"ignore", "mention_only", "candidate"}:
            raise HTTPException(400, "channel and a valid mode are required")
        return {"ok": True, "policy": system.filter.set_channel_mode(channel, mode).to_dict()}

    # ------------------------------------------------------------------ #
    # models: which AI does which job (config/models.toml)
    # ------------------------------------------------------------------ #
    def models_state(note: str = "") -> dict[str, Any]:
        state = catalog.describe(system.config.setup, demo_forced=system.config.demo_forced(),
                                 root=ROOT)
        state["note"] = note
        state["summary"] = system.provider_note
        return state

    @app.get("/api/models")
    async def get_models() -> dict[str, Any]:
        return models_state()

    @app.post("/api/models")
    async def save_models(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            setup = catalog.from_request(system.config.models_path, payload)
            # A key may only be stored under a name some model actually reads.
            usable = {e.key_env() for e in setup.models.values() if e.key_env()}
            for env_name, value in (payload.get("keys") or {}).items():
                if not str(value or "").strip():
                    continue
                if env_name not in usable or env_name.startswith("RIMURU_"):
                    raise ValueError(f"{env_name} is not the key of any model in the list.")
                catalog.set_key(system.config.env_path, env_name, str(value))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        catalog.save(setup)
        await system.apply_models()
        if system.config.demo_forced():
            note = "Saved. Rimuru was started with --demo, so it uses these after a restart."
        elif system.config.is_demo():
            note = "Saved, but Rimuru is still in demo mode: " + system.config.setup.check()[0]
        else:
            router = system.config.setup.entry_for("router")
            worker = system.config.setup.entry_for("worker")
            note = (f"Saved. Rimuru now thinks with {router.model}; new clones use "
                    f"{worker.model}. Clones already working finish on their model.")
        return models_state(note)

    @app.post("/api/models/find")
    async def find_models(payload: dict[str, Any]) -> dict[str, Any]:
        """Which models can this key use? Fills the model list in the panel."""
        provider = str(payload.get("provider") or "")
        preset = catalog.PROVIDERS.get(provider)
        if preset is None:
            raise HTTPException(400, "Choose a provider first.")
        base_url = str(payload.get("base_url") or "").strip().rstrip("/")
        key = str(payload.get("key") or "").strip()
        if not key and preset.key_env:
            # A key already stored goes only where it is meant to: the provider's
            # own address, or one already saved for that provider.
            saved = {e.base_url.rstrip("/") for e in system.config.setup.models.values()
                     if e.provider == provider and e.base_url}
            if base_url and base_url != preset.base_url.rstrip("/") and base_url not in saved:
                raise HTTPException(400, "Paste the key for this address.")
            env = str(payload.get("api_key_env") or "").strip()
            probe = catalog.ModelEntry(name="probe", provider=provider, model="",
                                       api_key_env=env if catalog.ENV_RE.fullmatch(env) else "")
            key = probe.key()
        try:
            found_url, ids = await catalog.list_models(provider, key=key, base_url=base_url)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from None
        return {"base_url": found_url, "models": ids}

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
