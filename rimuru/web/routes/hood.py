"""Under the hood: every message, session, step and decision, and the controls."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ... import persona
from ... import sessions as chats
from ...models import TERMINAL
from ...recall import recall as do_recall
from .. import friendly
from ..shared import Web


def add_routes(app: FastAPI, web: Web) -> None:
    system, session_thread, last_said = web.system, web.session_thread, web.last_said

    @app.get("/api/overview")
    async def overview() -> dict[str, Any]:
        return system.overview()

    @app.get("/api/triggers")
    async def triggers(limit: int = 120, verdict: str | None = None) -> dict[str, Any]:
        items = [t.to_dict() for t in system.store.recent_triggers(limit=400)]
        if verdict:
            items = [t for t in items if t["verdict"] == verdict]
        return {"items": items[:limit]}

    CHANNEL_NAMES = {"console": "Your chat", "telegram": "Telegram", "telegram-group": "Telegram group",
                     "gh": "GitHub", "github": "GitHub (practice)", "im": "Team chat (practice)"}
    MESSAGE_GROUPS = {
        "you": lambda t: t.kind == "human_direct",
        "telegram": lambda t: t.channel.startswith("telegram"),
        "github": lambda t: t.channel == "gh",
        "practice": lambda t: t.channel in {"im", "github"},
        "dropped": lambda t: t.verdict in {"dropped_fast", "dropped_slow"},
    }

    def message_fate(t) -> tuple[str, str]:
        """What happened to a message, in words - and a tone for the page."""
        if t.verdict == "dropped_fast":
            return f"Dropped by a simple rule: {friendly.plain(t.verdict_reason) or 'noise'}", "faint"
        if t.verdict == "dropped_slow":
            return f"The AI filter judged it noise: {friendly.plain(t.verdict_reason) or 'nothing to do'}", "faint"
        if t.route == "passthrough":
            names = [persona.of(w)[0] for w in (system.store.sessions.get(i) for i in t.routed_to) if w]
            return f"Went straight to {', '.join(names) or 'a clone'} - the clone already on it", "good"
        if t.route == "router":
            started = [x for x in system.store.tasks.values() if x.origin_trigger_id == t.id]
            if started:
                clone = system.store.sessions.get(started[0].worker_id or "")
                who = persona.of(clone)[0] if clone else "a clone"
                return f"Reached Rimuru, who put {who} on it: “{started[0].title}”", "good"
            return ("Reached Rimuru" + (" - your message, straight through" if t.kind == "human_direct" else
                                        " - the AI filter let it through")), "said"
        return "Waiting to be looked at", ""

    @app.get("/api/messages")
    async def list_messages(show: str = "all", limit: int = 60, before: float | None = None) -> dict[str, Any]:
        """Every stored message, newest first - so the page is full after a restart."""
        every = sorted(system.store.triggers.values(), key=lambda t: t.ts, reverse=True)
        counts = {"all": len(every), **{k: sum(1 for t in every if f(t)) for k, f in MESSAGE_GROUPS.items()}}
        picked = [t for t in every if show == "all" or MESSAGE_GROUPS.get(show, lambda _t: False)(t)]
        if before is not None:
            picked = [t for t in picked if t.ts < before]
        conv = system.config.conversation_channels
        items = []
        for t in picked[:max(1, min(limit, 200))]:
            fate, tone = message_fate(t)
            thread = system.store.threads.get(t.thread_id or "")
            items.append({
                "id": t.id, "ts": t.ts, "channel": CHANNEL_NAMES.get(t.channel, t.channel),
                "author": "You" if t.kind == "human_direct" and t.channel == "console" else t.author,
                "text": friendly.plain(t.content)[:400], "fate": fate, "tone": tone,
                "skills": t.meta.get("skills") or [],
                "session": ({"key": thread.key, "title": chats.title_of(system.store, thread)}
                            if thread is not None and thread.channel in conv else None),
                "thread": thread.title if thread is not None and thread.channel not in conv else "",
            })
        return {"items": items, "counts": counts, "more": len(picked) > len(items)}

    @app.get("/api/threads")
    async def threads(limit: int = 100) -> dict[str, Any]:
        items = sorted(system.store.threads.values(), key=lambda t: t.last_ts, reverse=True)
        return {"items": [t.to_dict() for t in items[:max(1, limit)]], "total": len(items)}

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
        payload["jobs"] = [j.to_dict() for j in system.store.jobs.of(task.worker_id)]
        return payload

    @app.get("/api/sessions")
    async def sessions(limit: int = 200) -> dict[str, Any]:
        """Rimuru, the watchdog, every clone at work, and the latest others."""
        every = sorted(system.store.sessions.values(),
                       key=lambda s: (s.kind != "router", s.kind != "watchdog", -s.created_ts))
        picked = [s for i, s in enumerate(every) if i < limit or s.status not in TERMINAL]
        return {"items": [{**s.to_dict(), "summary": None} for s in picked], "total": len(every)}

    def picked_turns(session: str) -> tuple[list, list]:
        turns = system.rimurus_turns()
        picked = [(s, keys) for s, keys in turns
                  if session == "all" or (session == "other" and not keys) or session in keys]
        return turns, picked

    @app.get("/api/rimuru/turns")
    async def rimuru_turns(session: str = "all", limit: int = 20) -> dict[str, Any]:
        """Each turn under the session(s) it was about; "other" for the ones about
        no session (a clone's progress, the watchdog, team chats)."""
        turns, picked = picked_turns(session)
        latest: dict[str, float] = {}
        counts: dict[str, int] = {}
        other = 0
        for step, keys in turns:
            other += not keys
            for key in keys:
                counts[key] = counts.get(key, 0) + 1
                latest[key] = step.ts
        titles: dict[str, str] = {}
        for key in counts:
            thread = session_thread(key)
            titles[key] = chats.title_of(system.store, thread) if thread else key
        groups = [{"key": "all", "title": "All", "count": len(turns)}]
        groups += [{"key": key, "title": titles[key], "count": counts[key]}
                   for key in sorted(counts, key=lambda k: -latest[k])]
        if other:
            groups.append({"key": "other", "title": "Not about a session", "count": other})
        shown = picked[-max(1, min(limit, 500)):]
        return {"groups": groups, "total": len(picked),
                "steps": [{**s.to_dict(), "about": sorted(titles[k] for k in keys)} for s, keys in shown]}

    @app.delete("/api/rimuru/turns")
    async def clear_rimuru_turns(session: str = "all") -> dict[str, Any]:
        """Clear turns from the log. Rimuru keeps what it has in mind, and the chat
        stays: to forget a session completely, delete the session."""
        _turns, picked = picked_turns(session)
        cleared = system.store.delete_steps(system.router.session.id, {s.id for s, _keys in picked})
        return {"ok": True, "cleared": cleared}

    @app.get("/api/sessions/{session_id}")
    async def session_detail(session_id: str, limit: int | None = None) -> dict[str, Any]:
        session = system.store.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, f"no session {session_id}")
        worker = system.pool.get(session_id)
        return {
            "session": session.to_dict(),
            "transcript": [s.to_dict() for s in system.store.transcript(session_id, last=limit)],
            "total": session.step_count,
            "task": (
                system.store.tasks[session.task_id].to_dict()
                if session.task_id and session.task_id in system.store.tasks else None
            ),
            "jobs": [j.to_dict() for j in system.store.jobs.of(session_id)],
            "queued": worker.loop.inbox.qsize() if worker else (
                system.router.loop.inbox.qsize() if session_id == "rtr_main" else 0
            ),
            "speculative": bool(worker.speculative) if worker else False,
        }

    @app.get("/api/jobs")
    async def jobs() -> dict[str, Any]:
        return {"items": [j.to_dict() for j in system.store.jobs.recent(120)]}

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

    @app.get("/api/settings/details")
    async def details_setting() -> dict[str, Any]:
        return {"days": system.keep_details_days()}

    @app.post("/api/settings/details")
    async def set_details_setting(payload: dict[str, Any]) -> dict[str, Any]:
        """How long old jobs keep the raw detail of every step (0: for ever)."""
        try:
            days = int(payload.get("days"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Choose a number of days.") from None
        if not 0 <= days <= 3650:
            raise HTTPException(400, "Choose a number of days.")
        system.store.set_setting("keep_details_days", str(days))
        return {"days": days}

    @app.get("/api/memory")
    async def memory(limit: int = 30) -> dict[str, Any]:
        """What was done, session by session (the latest `limit` sessions): each
        one's clones and the job each did - to follow the story without any IDs."""
        store = system.store
        conv = system.config.conversation_channels
        every = sorted((t for t in store.threads.values() if chats.is_session(t, system.config)
                        and (store.chat_of(t.key) or store.tasks_of(t.id))), key=last_said, reverse=True)
        groups: dict[str, dict[str, Any]] = {}
        for thread in every[:max(1, limit)]:
            folder = chats.folder_for(system.config, thread)
            groups[thread.id] = {
                "key": thread.key, "title": chats.title_of(store, thread),
                "messages": len(store.chat_of(thread.key)),
                "last_ts": last_said(thread), "clones": [],
                "folder": chats.display(folder) if folder.exists() else None, "folder_abs": str(folder),
            }
        other = {"key": None, "title": "Practice runs and team channels", "messages": 0, "last_ts": 0.0,
                 "clones": [], "folder": chats.display(system.config.workspace),
                 "folder_abs": str(system.config.workspace)}
        in_chats = {t.id for t in store.threads.values() if chats.is_session(t, system.config)}
        outside = 0
        for worker in sorted(store.sessions_of_kind("worker"), key=lambda s: s.created_ts, reverse=True):
            task = store.tasks.get(worker.task_id or "")
            thread_id = (task.thread_id if task else None) or ""
            group = groups.get(thread_id)
            if group is None:
                # A session too old to be listed - or practice work, the latest of it.
                if thread_id in in_chats or outside >= limit:
                    continue
                outside += 1
                group = other
            card = friendly.worker_card(store, worker, conv)
            group["clones"].append(card)
            group["last_ts"] = max(group["last_ts"], card["started_ts"] or 0)
        sessions = sorted((g for g in groups.values() if g["messages"] or g["clones"]),
                          key=lambda g: g["last_ts"], reverse=True)
        for group in [*sessions, other]:
            group["clones"].sort(key=lambda c: c["started_ts"] or 0)     # in the order they happened
        return {"sessions": sessions, "other": other, "total": len(every)}

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
        for mem in store.memories.values():
            if found(mem.text):
                items.append({"kind": "note", "id": None, "title": mem.text,
                              "sub": "Something Rimuru remembers", "ts": mem.created_ts})
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
        steps = system.store.transcript("rtr_main", last=300)
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
