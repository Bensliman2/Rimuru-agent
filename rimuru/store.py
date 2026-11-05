"""State, held in memory and written through to SQLite.

Article s5.1 argues the Agent Loop should be stateless and all durable state
should live outside it, so a restarted or replaced container does not lose the
continuity of sessions and tasks. This Store is that boundary: the engine never
holds state anywhere else, so swapping SQLite for Postgres later is a change to
this file alone.

Reads come from the in-memory mirror (the UI polls hard), writes go to both.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .ids import new_id
from .models import (
    R_PASSTHROUGH,
    V_ACCEPTED,
    Job,
    Session,
    Step,
    Task,
    Thread,
    Trigger,
    TERMINAL,
    Usage,
    from_dict,
    now,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    id TEXT PRIMARY KEY, ts REAL, channel TEXT, thread_key TEXT, thread_id TEXT,
    author TEXT, kind TEXT, content TEXT, verdict TEXT, verdict_reason TEXT,
    route TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY, channel TEXT, key TEXT, title TEXT,
    created_ts REAL, last_ts REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, title TEXT, status TEXT, worker_id TEXT,
    created_ts REAL, started_ts REAL, finished_ts REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, kind TEXT, title TEXT, model TEXT, status TEXT,
    task_id TEXT, created_ts REAL, updated_ts REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY, session_id TEXT, idx INTEGER, ts REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, session_id TEXT, status TEXT, started_ts REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS notes (ts REAL, note TEXT);
CREATE TABLE IF NOT EXISTS chat (
    id TEXT PRIMARY KEY, ts REAL, role TEXT, text TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS steps_session ON steps(session_id, idx);
CREATE INDEX IF NOT EXISTS triggers_thread ON triggers(thread_id);
CREATE INDEX IF NOT EXISTS triggers_ts ON triggers(ts);
"""


class Store:
    def __init__(self, db_path: Path | str, bus: Any = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.bus = bus
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

        # In-memory mirror, authoritative for the running process.
        self.triggers: dict[str, Trigger] = {}
        self.threads: dict[str, Thread] = {}
        self.thread_by_key: dict[str, str] = {}
        self.tasks: dict[str, Task] = {}
        self.sessions: dict[str, Session] = {}
        self.steps: dict[str, list[Step]] = {}
        self.jobs: dict[str, Job] = {}

        # Spill area (article s3.3): Triggers that passed straight through to a
        # Worker are NOT injected into the Router context, but the Router can
        # still reach them with recall(), so it keeps global awareness cheaply.
        self.router_spill: list[str] = []

        # Global memory. The Router is the system's memory (s2.1), and these are
        # the facts it has chosen to keep across tasks and sessions.
        self.notes: list[dict[str, Any]] = []

        # The person's conversation with Rimuru.
        self.chat: list[dict[str, Any]] = []

        # Reload everything that was saved. The loops are stateless (article
        # s5.1), so a restart must find the same tasks, sessions, transcripts,
        # memory and chat it left. The evaluation caught a crash silently
        # erasing a running job back when only the chat was reloaded.
        self._load()

    def _load(self) -> None:
        def rows(sql: str) -> list[dict[str, Any]]:
            return [json.loads(r[0]) for r in self._conn.execute(sql)]

        for d in rows("SELECT data FROM triggers ORDER BY ts"):
            trigger = from_dict(Trigger, d)
            self.triggers[trigger.id] = trigger
        for d in rows("SELECT data FROM threads ORDER BY created_ts"):
            thread = from_dict(Thread, d)
            self.threads[thread.id] = thread
            self.thread_by_key[f"{thread.channel}:{thread.key}"] = thread.id
        for d in rows("SELECT data FROM tasks ORDER BY created_ts"):
            task = from_dict(Task, d)
            self.tasks[task.id] = task
        for d in rows("SELECT data FROM sessions ORDER BY created_ts"):
            session = from_dict(Session, d)
            self.sessions[session.id] = session
        for d in rows("SELECT data FROM steps ORDER BY session_id, idx"):
            step = from_dict(Step, d)
            self.steps.setdefault(step.session_id, []).append(step)
        for d in rows("SELECT data FROM jobs ORDER BY started_ts"):
            job = from_dict(Job, d)
            self.jobs[job.id] = job
        for ts, note in self._conn.execute("SELECT ts, note FROM notes ORDER BY ts"):
            self.notes.append({"ts": ts, "note": note})
        self.chat.extend(rows("SELECT data FROM chat ORDER BY ts"))

    def add_chat(self, role: str, text: str, **extra: Any) -> dict[str, Any]:
        entry = {"id": new_id("step"), "ts": now(), "role": role, "text": text, **extra}
        self.chat.append(entry)
        self._write("INSERT OR REPLACE INTO chat (id, ts, role, text, data) VALUES (?,?,?,?,?)",
                    (entry["id"], entry["ts"], role, text, json.dumps(entry)))
        if self.bus:
            self.bus.emit("chat.message", message=entry)
        return entry

    def add_note(self, note: str) -> dict[str, Any]:
        entry = {"ts": now(), "note": note}
        self.notes.append(entry)
        self._write("INSERT INTO notes (ts, note) VALUES (?,?)", (entry["ts"], note))
        if self.bus:
            self.bus.emit("memory.note", **entry)
        return entry

    # ------------------------------------------------------------------ #
    # low-level
    # ------------------------------------------------------------------ #
    def _write(self, sql: str, params: Iterable[Any]) -> None:
        with self._lock:
            if self._closed:
                # Loops are allowed to outlive the store during shutdown; their
                # in-memory bookkeeping still works, there is just nothing left
                # to persist to.
                return
            try:
                self._conn.execute(sql, tuple(params))
                self._conn.commit()
            except sqlite3.Error:
                # Durability is not worth taking a live session down for.
                pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------ #
    # triggers
    # ------------------------------------------------------------------ #
    def put_trigger(self, trigger: Trigger) -> Trigger:
        self.triggers[trigger.id] = trigger
        self._write(
            "INSERT OR REPLACE INTO triggers "
            "(id, ts, channel, thread_key, thread_id, author, kind, content, "
            " verdict, verdict_reason, route, data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trigger.id, trigger.ts, trigger.channel, trigger.thread_key,
                trigger.thread_id, trigger.author, trigger.kind, trigger.content,
                trigger.verdict, trigger.verdict_reason, trigger.route,
                json.dumps(trigger.to_dict()),
            ),
        )
        return trigger

    def get_trigger(self, trigger_id: str) -> Trigger | None:
        return self.triggers.get(trigger_id)

    def recent_triggers(self, limit: int = 120) -> list[Trigger]:
        return sorted(self.triggers.values(), key=lambda t: t.ts, reverse=True)[:limit]

    # ------------------------------------------------------------------ #
    # threads
    # ------------------------------------------------------------------ #
    def get_or_create_thread(self, channel: str, key: str, title: str) -> Thread:
        composite = f"{channel}:{key}"
        existing_id = self.thread_by_key.get(composite)
        if existing_id:
            return self.threads[existing_id]
        thread = Thread(id=new_id("thread"), channel=channel, key=key, title=title)
        self.threads[thread.id] = thread
        self.thread_by_key[composite] = thread.id
        self._persist_thread(thread)
        if self.bus:
            self.bus.emit("thread.created", thread=thread.to_dict())
        return thread

    def _persist_thread(self, thread: Thread) -> None:
        self._write(
            "INSERT OR REPLACE INTO threads "
            "(id, channel, key, title, created_ts, last_ts, data) VALUES (?,?,?,?,?,?,?)",
            (thread.id, thread.channel, thread.key, thread.title,
             thread.created_ts, thread.last_ts, json.dumps(thread.to_dict())),
        )

    def attach_trigger_to_thread(self, thread: Thread, trigger: Trigger) -> None:
        trigger.thread_id = thread.id
        if trigger.id not in thread.trigger_ids:
            thread.trigger_ids.append(trigger.id)
        thread.last_ts = trigger.ts
        self._persist_thread(thread)

    def subscribe(self, thread_id: str, session_id: str) -> bool:
        """Many-to-many subscription between a Thread and a Worker (s3.2)."""
        thread = self.threads.get(thread_id)
        session = self.sessions.get(session_id)
        if not thread or not session:
            return False
        changed = False
        if session_id not in thread.subscribers:
            thread.subscribers.append(session_id)
            changed = True
        if thread_id not in session.subscriptions:
            session.subscriptions.append(thread_id)
            changed = True
        if changed:
            self._persist_thread(thread)
            self.touch_session(session)
            if self.bus:
                self.bus.emit(
                    "thread.subscribed",
                    thread_id=thread_id, session_id=session_id,
                    thread_title=thread.title,
                )
        return changed

    def unsubscribe(self, thread_id: str, session_id: str) -> bool:
        thread = self.threads.get(thread_id)
        session = self.sessions.get(session_id)
        changed = False
        if thread and session_id in thread.subscribers:
            thread.subscribers.remove(session_id)
            self._persist_thread(thread)
            changed = True
        if session and thread_id in session.subscriptions:
            session.subscriptions.remove(thread_id)
            self.touch_session(session)
            changed = True
        if changed and self.bus:
            self.bus.emit("thread.unsubscribed", thread_id=thread_id, session_id=session_id)
        return changed

    def subscribers_of(self, thread_id: str) -> list[str]:
        thread = self.threads.get(thread_id)
        if not thread:
            return []
        # Only live sessions count: a finished Worker must not swallow events.
        return [
            s for s in thread.subscribers
            if (sess := self.sessions.get(s)) and sess.status not in TERMINAL
        ]

    # ------------------------------------------------------------------ #
    # sessions
    # ------------------------------------------------------------------ #
    def put_session(self, session: Session) -> Session:
        self.sessions[session.id] = session
        self.steps.setdefault(session.id, [])
        self._persist_session(session)
        if self.bus:
            self.bus.emit("session.created", session=session.to_dict())
        return session

    def _persist_session(self, session: Session) -> None:
        self._write(
            "INSERT OR REPLACE INTO sessions "
            "(id, kind, title, model, status, task_id, created_ts, updated_ts, data) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (session.id, session.kind, session.title, session.model, session.status,
             session.task_id, session.created_ts, session.updated_ts,
             json.dumps(session.to_dict())),
        )

    def touch_session(self, session: Session, **changes: Any) -> Session:
        for key, value in changes.items():
            setattr(session, key, value)
        session.updated_ts = now()
        self._persist_session(session)
        if self.bus:
            self.bus.emit("session.updated", session=session.to_dict())
        return session

    def sessions_of_kind(self, kind: str) -> list[Session]:
        return [s for s in self.sessions.values() if s.kind == kind]

    def live_workers(self) -> list[Session]:
        """Workers actually running. Queued ones hold a slot in the backlog, not a loop."""
        return [
            s for s in self.sessions.values()
            if s.kind == "worker" and s.status != "queued" and s.status not in TERMINAL
        ]

    def queued_workers(self) -> list[Session]:
        return [s for s in self.sessions.values() if s.kind == "worker" and s.status == "queued"]

    # ------------------------------------------------------------------ #
    # steps
    # ------------------------------------------------------------------ #
    def add_step(self, step: Step) -> Step:
        self.steps.setdefault(step.session_id, []).append(step)
        session = self.sessions.get(step.session_id)
        if session:
            session.step_count = len(self.steps[step.session_id])
            session.usage.add(step.usage)
            self._persist_session(session)
        self._write(
            "INSERT OR REPLACE INTO steps (id, session_id, idx, ts, data) VALUES (?,?,?,?,?)",
            (step.id, step.session_id, step.index, step.ts, json.dumps(step.to_dict())),
        )
        if self.bus:
            self.bus.emit("step.added", session_id=step.session_id, step=step.to_dict())
        return step

    def transcript(self, session_id: str) -> list[Step]:
        return self.steps.get(session_id, [])

    # ------------------------------------------------------------------ #
    # tasks
    # ------------------------------------------------------------------ #
    def put_task(self, task: Task) -> Task:
        self.tasks[task.id] = task
        self._persist_task(task)
        if self.bus:
            self.bus.emit("task.created", task=task.to_dict())
        return task

    def _persist_task(self, task: Task) -> None:
        self._write(
            "INSERT OR REPLACE INTO tasks "
            "(id, title, status, worker_id, created_ts, started_ts, finished_ts, data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (task.id, task.title, task.status, task.worker_id, task.created_ts,
             task.started_ts, task.finished_ts, json.dumps(task.to_dict())),
        )

    def update_task(self, task: Task, **changes: Any) -> Task:
        for key, value in changes.items():
            setattr(task, key, value)
        self._persist_task(task)
        if self.bus:
            self.bus.emit("task.updated", task=task.to_dict())
        return task

    def all_tasks(self) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda t: t.created_ts, reverse=True)

    # ------------------------------------------------------------------ #
    # jobs (background tool calls)
    # ------------------------------------------------------------------ #
    def put_job(self, job: Job) -> Job:
        self.jobs[job.id] = job
        self._write(
            "INSERT OR REPLACE INTO jobs (id, session_id, status, started_ts, data) "
            "VALUES (?,?,?,?,?)",
            (job.id, job.session_id, job.status, job.started_ts, json.dumps(job.to_dict())),
        )
        if self.bus:
            self.bus.emit("job.started", job=job.to_dict())
        return job

    def update_job(self, job: Job, **changes: Any) -> Job:
        for key, value in changes.items():
            setattr(job, key, value)
        self._write(
            "INSERT OR REPLACE INTO jobs (id, session_id, status, started_ts, data) "
            "VALUES (?,?,?,?,?)",
            (job.id, job.session_id, job.status, job.started_ts, json.dumps(job.to_dict())),
        )
        if self.bus:
            self.bus.emit("job.updated", job=job.to_dict())
        return job

    # ------------------------------------------------------------------ #
    # aggregate stats for the dashboard
    # ------------------------------------------------------------------ #
    def _requested_at(self, task: Task) -> float:
        origin = self.triggers.get(task.origin_trigger_id or "")
        return origin.ts if origin else task.created_ts

    def stats(self) -> dict[str, Any]:
        triggers = list(self.triggers.values())
        verdicts: dict[str, int] = {}
        for t in triggers:
            verdicts[t.verdict] = verdicts.get(t.verdict, 0) + 1

        passthrough = sum(1 for t in triggers if t.route == R_PASSTHROUGH)
        via_router = sum(1 for t in triggers if t.route and t.route != R_PASSTHROUGH)

        total_usage = Usage()
        for session in self.sessions.values():
            total_usage.add(session.usage)

        tasks = list(self.tasks.values())
        # Measured like the article's s7.1: from the work request to its result,
        # so time spent queued for a worker slot counts against the system.
        durations = sorted(
            t.finished_ts - self._requested_at(t)
            for t in tasks if t.status == "done" and t.finished_ts
        )

        def pct(values: list[float], q: float) -> float | None:
            if not values:
                return None
            idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
            return values[idx]

        router_steps = self.steps.get("rtr_main", [])
        batched = [s.batch_size for s in router_steps if s.batch_size > 0]

        return {
            "triggers_total": len(triggers),
            "verdicts": verdicts,
            "accepted": verdicts.get(V_ACCEPTED, 0),
            "route_passthrough": passthrough,
            "route_router": via_router,
            "tasks_total": len(tasks),
            "tasks_by_status": {
                s: sum(1 for t in tasks if t.status == s)
                for s in ("queued", "running", "done", "failed", "cancelled")
            },
            "task_p50": pct(durations, 0.5),
            "task_p95": pct(durations, 0.95),
            "sessions_total": len(self.sessions),
            "workers_live": len(self.live_workers()),
            "workers_queued": len(self.queued_workers()),
            "usage": total_usage.to_dict(),
            "router_steps": len(router_steps),
            "router_avg_batch": (sum(batched) / len(batched)) if batched else 0.0,
            "router_max_batch": max(batched) if batched else 0,
            "spill_size": len(self.router_spill),
        }
