"""State, held in memory and written through to SQLite.

Article s5.1 argues the Agent Loop should be stateless and all durable state
should live outside it, so a restarted or replaced container does not lose the
continuity of sessions and tasks. This Store is that boundary: the engine never
holds state anywhere else, so swapping SQLite for Postgres later is a change to
this file alone.

Reads come from the in-memory mirror (the UI polls hard), writes go to both.
The mirror holds what stays small - sessions, jobs, messages. What grows without
limit - every step of every clone, every command's output - is read from the
database when someone wants it, so a restart and memory use do not grow with the
history (StepCache, JobRecords).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Iterable

from .ids import new_id
from .models import (
    R_PASSTHROUGH,
    V_ACCEPTED,
    Finding,
    Job,
    Memory,
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

log = logging.getLogger("rimuru.store")

# Raised whenever the tables change. A database from before is backed up first
# (data/backups) and then brought up to date; one from after is refused.
# A new table an older Rimuru simply ignores (findings) does not count.
SCHEMA_VERSION = 1

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
CREATE TABLE IF NOT EXISTS contexts (session_id TEXT PRIMARY KEY, ts REAL, data TEXT);
CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, ts REAL, data TEXT);
CREATE TABLE IF NOT EXISTS findings (id TEXT PRIMARY KEY, ts REAL, scope TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, topic TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS steps_session ON steps(session_id, idx);
CREATE INDEX IF NOT EXISTS steps_ts ON steps(ts);
CREATE INDEX IF NOT EXISTS jobs_session ON jobs(session_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS triggers_thread ON triggers(thread_id);
CREATE INDEX IF NOT EXISTS triggers_ts ON triggers(ts);
"""


_DEFAULT_CHAT = "direct"            # the session of a line that names none (sessions.DEFAULT)


def _lean(step: Step) -> None:
    """A step keeps what the pages show. The whole text of a file a clone wrote
    is on disk: here, only its beginning and how long it was. (New dicts: the
    originals may still be part of the conversation with the model.)"""
    calls = []
    for call in step.tool_calls:
        args = dict(call.get("args") or {})
        for key in ("content", "old", "new"):
            text = args.get(key)
            if isinstance(text, str) and len(text) > _KEEP_IN_STEP:
                if key == "content":
                    args["lines"] = len(text.splitlines())
                args[key] = text[:_KEEP_IN_STEP] + f"\n... ({len(text) - _KEEP_IN_STEP} more characters)"
        calls.append({**call, "args": args})
    step.tool_calls = calls


_KEEP_IN_STEP = 1500


def _trim_step(step: Step) -> None:
    """An old step: enough to follow what happened, not every byte of it."""
    step.injected = step.injected[:400]
    step.thinking = ""
    step.text = step.text[:400]
    # The end of an output is what says how it went ("44 passed").
    step.tool_results = [{**r, "output": str(r.get("output", ""))[-300:]} for r in step.tool_results]
    step.tool_calls = [{**c, "args": {k: v[:300] if isinstance(v, str) else v for k, v in (c.get("args") or {}).items()}}
                       for c in step.tool_calls]


class StepCache:
    """Each session's steps: read from the database when first wanted, and kept
    only for the sessions used most recently. Everything is on disk the moment it
    happens, so forgetting one here loses nothing."""

    def __init__(self, load: Callable[[str], list[Step]], keep: int = 64) -> None:
        self._load, self._keep = load, keep
        self._lists: OrderedDict[str, list[Step]] = OrderedDict()

    def peek(self, session_id: str) -> list[Step] | None:
        return self._lists.get(session_id)

    def get(self, session_id: str, default: list[Step] | None = None) -> list[Step]:
        if session_id not in self._lists:
            loaded = self._load(session_id)
            if not loaded:
                return [] if default is None else default
            self[session_id] = loaded
        self._lists.move_to_end(session_id)
        return self._lists[session_id]

    def __setitem__(self, session_id: str, steps: list[Step]) -> None:
        self._lists[session_id] = steps
        self._lists.move_to_end(session_id)
        while len(self._lists) > self._keep:
            self._lists.popitem(last=False)

    def pop(self, session_id: str, default: Any = None) -> Any:
        return self._lists.pop(session_id, default)


class JobRecords:
    """Background commands: the ones still running are in memory, and the rest -
    with their output - are read from the database when someone wants them."""

    def __init__(self, store: "Store") -> None:
        self._store = store
        self.running: dict[str, Job] = {}

    def remember(self, job: Job) -> None:
        if job.status == "running":
            self.running[job.id] = job
        else:
            self.running.pop(job.id, None)

    def get(self, job_id: str, default: Any = None) -> Any:
        if job_id in self.running:
            return self.running[job_id]
        found = self._store._jobs_where("id = ?", (job_id,))
        return found[0] if found else default

    def of(self, session_id: str | None) -> list[Job]:
        """One clone's commands, the running ones as they are right now."""
        rows = self._store._jobs_where("session_id = ?", (session_id or "",))
        return [self.running.get(j.id, j) for j in rows]

    def recent(self, limit: int) -> list[Job]:
        rows = self._store._jobs_where(f"1 = 1 ORDER BY started_ts DESC LIMIT {int(limit)}")
        return [self.running.get(j.id, j) for j in rows]

    def values(self) -> list[Job]:
        return [self.running.get(j.id, j) for j in self._store._jobs_where("1 = 1")]

    def __bool__(self) -> bool:
        return bool(self.running) or bool(self._store._jobs_where("1 = 1 LIMIT 1"))

    def __len__(self) -> int:
        return len(self.values())

    def pop(self, job_id: str, default: Any = None) -> Any:
        return self.running.pop(job_id, default)


class Store:
    def __init__(self, db_path: Path | str, bus: Any = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.bus = bus
        self._lock = threading.RLock()
        self._closed = False
        self.save_problem = ""               # said to the person while it is true
        self._save_logged = 0.0
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # A commit no longer waits for the disk each time, and reading never waits
        # for a write. An app crash loses nothing; a power cut at most the last moments.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._check_version()

        # In-memory mirror, authoritative for the running process.
        self.triggers: dict[str, Trigger] = {}
        self.threads: dict[str, Thread] = {}
        self.thread_by_key: dict[str, str] = {}
        self.tasks: dict[str, Task] = {}
        self.sessions: dict[str, Session] = {}
        self.steps = StepCache(self._load_steps)
        self.jobs = JobRecords(self)
        # Each session's jobs and messages, found without scanning everything.
        self._tasks_by_thread: dict[str, list[Task]] = {}
        self._chat_by_key: dict[str, list[dict[str, Any]]] = {}

        # Spill area (article s3.3): Triggers that passed straight through to a
        # Worker are NOT injected into the Router context, but the Router can
        # still reach them with recall(), so it keeps global awareness cheaply.
        self.router_spill: list[str] = []

        # Global memory. The Router is the system's memory (s2.1), and these are
        # the facts it has chosen to keep across tasks and sessions.
        self.notes: list[dict[str, Any]] = []
        # Long-term memory (s8.1): what Rimuru remembers, read back to it each turn.
        self.memories: dict[str, Memory] = {}
        # The session notebooks (rimuru/notebook.py): what clones found out, shared.
        self.findings: dict[str, Finding] = {}

        # The person's conversation with Rimuru.
        self.chat: list[dict[str, Any]] = []
        self._events_added = 0
        self._router_recent: tuple[int, list[Step]] = (-1, [])

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
            self._index_task(task)
        for d in rows("SELECT data FROM sessions ORDER BY created_ts"):
            session = from_dict(Session, d)
            self.sessions[session.id] = session
        for d in rows("SELECT data FROM jobs WHERE status = 'running'"):
            job = from_dict(Job, d)
            self.jobs.running[job.id] = job
        for ts, note in self._conn.execute("SELECT ts, note FROM notes ORDER BY ts"):
            self.notes.append({"ts": ts, "note": note})
        for d in rows("SELECT data FROM memories ORDER BY ts"):
            memory = from_dict(Memory, d)
            self.memories[memory.id] = memory
        for d in rows("SELECT data FROM findings ORDER BY ts"):
            finding = from_dict(Finding, d)
            self.findings[finding.id] = finding
        if self.notes and not self.memories:
            # Notes were write-only: kept, never read back. They become memories once.
            for note in self.notes:
                self.put_memory(Memory(id=new_id("memory"), text=note["note"], about="general",
                                       created_ts=note["ts"]), quiet=True)
        self.chat.extend(rows("SELECT data FROM chat ORDER BY ts"))
        for line in self.chat:
            self._chat_by_key.setdefault(line.get("chat", _DEFAULT_CHAT), []).append(line)

    def add_chat(self, role: str, text: str, **extra: Any) -> dict[str, Any]:
        entry = {"id": new_id("step"), "ts": now(), "role": role, "text": text, **extra}
        self.chat.append(entry)
        self._chat_by_key.setdefault(entry.get("chat", _DEFAULT_CHAT), []).append(entry)
        self._write("INSERT OR REPLACE INTO chat (id, ts, role, text, data) VALUES (?,?,?,?,?)",
                    (entry["id"], entry["ts"], role, text, json.dumps(entry)))
        if self.bus:
            self.bus.emit("chat.message", message=entry)
        return entry

    # ------------------------------------------------------------------ #
    # the story the pages show (bus.STORY): kept, within a limit
    # ------------------------------------------------------------------ #
    def add_event(self, event: dict[str, Any], *, keep: int = 5000) -> None:
        self._write("INSERT INTO events (ts, topic, data) VALUES (?,?,?)",
                    (event["ts"], event["topic"], json.dumps(event, default=str)))
        self._events_added += 1
        if self._events_added % 200 == 0:
            self._write("DELETE FROM events WHERE seq <= (SELECT MAX(seq) - ? FROM events)", (keep,))

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def forget_events(self, marks: Iterable[str]) -> None:
        """Every saved event that mentions any of these (a deleted session's IDs)."""
        for mark in marks:
            self._write("DELETE FROM events WHERE instr(data, ?) > 0", (mark,))

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
            except sqlite3.Error as exc:
                self._could_not_save(exc)     # not worth taking a live session down for
            else:
                self.save_problem = ""

    def _could_not_save(self, exc: sqlite3.Error) -> None:
        """The work carries on, but the person has to know it is not being kept."""
        reason = "the disk is full" if "disk is full" in str(exc) else str(exc)
        self.save_problem = (f"Rimuru cannot save right now: {reason}. It keeps working, but what "
                             f"happens from now on would be lost if it restarted.")
        if time.time() - self._save_logged > 60:
            self._save_logged = time.time()
            log.error("could not write to the database: %s", exc)

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

    def put_thread(self, thread: Thread) -> Thread:
        self._persist_thread(thread)
        if self.bus:
            self.bus.emit("thread.updated", thread_id=thread.id)
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
    def delete_steps(self, session_id: str, ids: set[str]) -> int:
        """Clear turns from a session's log (Under the hood -> Rimuru -> Clear)."""
        current = self.transcript(session_id)
        kept = [s for s in current if s.id not in ids]
        gone = len(current) - len(kept)
        self.steps[session_id] = kept
        self._delete("steps", "id", sorted(ids))
        if (session := self.sessions.get(session_id)) is not None:
            session.step_count = len(kept)
            self._persist_session(session)
        return gone

    def save_step(self, step: Step) -> None:
        """Rewrite a step already recorded (deleting a session scrubs Rimuru's turns)."""
        self._write(
            "INSERT OR REPLACE INTO steps (id, session_id, idx, ts, data) VALUES (?,?,?,?,?)",
            (step.id, step.session_id, step.index, step.ts, json.dumps(step.to_dict())),
        )

    def add_step(self, step: Step) -> Step:
        _lean(step)
        loaded = self.steps.peek(step.session_id)
        if loaded is not None:
            loaded.append(step)
        session = self.sessions.get(step.session_id)
        if session:
            session.step_count += 1
            session.usage.add(step.usage)
            self._persist_session(session)
        self._write(
            "INSERT OR REPLACE INTO steps (id, session_id, idx, ts, data) VALUES (?,?,?,?,?)",
            (step.id, step.session_id, step.index, step.ts, json.dumps(step.to_dict())),
        )
        if self.bus:
            self.bus.emit("step.added", session_id=step.session_id, step=step.to_dict())
        return step

    def transcript(self, session_id: str, *, last: int | None = None) -> list[Step]:
        """A session's steps, oldest first - or only its `last` few."""
        if last is None:
            return self.steps.get(session_id)
        loaded = self.steps.peek(session_id)
        return loaded[-last:] if loaded is not None else self._load_steps(session_id, last=last)

    def _load_steps(self, session_id: str, *, last: int | None = None) -> list[Step]:
        newest = "" if last is None else f" DESC LIMIT {int(last)}"
        with self._lock:
            if self._closed:
                return []
            rows = self._conn.execute(
                f"SELECT data FROM steps WHERE session_id = ? ORDER BY idx{newest}", (session_id,)).fetchall()
        steps = [from_dict(Step, json.loads(r[0])) for r in rows]
        return steps[::-1] if last is not None else steps

    def setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (f"setting:{key}",)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._write("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (f"setting:{key}", value))

    def keep_summary(self, session: Session, summary: dict[str, Any]) -> None:
        """What a finished clone did, saved so it never has to be worked out again."""
        session.summary = summary
        self._persist_session(session)

    def _jobs_where(self, condition: str, args: tuple[Any, ...] = ()) -> list[Job]:
        with self._lock:
            if self._closed:
                return []
            rows = self._conn.execute(f"SELECT data FROM jobs WHERE {condition}", args).fetchall()
        return [from_dict(Job, json.loads(r[0])) for r in rows]

    def trim_old_details(self, days: int, *, batch: int = 400) -> int:
        """Old jobs keep their summary, files and result; the raw detail of each
        step (what it was given, its reasoning, tool output) is cut down after
        `days`. 0 keeps everything. Only steps not trimmed before are touched."""
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = 'trimmed_until'").fetchone()
        since, trimmed = float(row[0]) if row else 0.0, 0
        while True:
            with self._lock:
                if self._closed:
                    return trimmed
                rows = self._conn.execute(
                    "SELECT id, session_id, idx, ts, data FROM steps WHERE ts > ? AND ts <= ? ORDER BY ts LIMIT ?",
                    (since, cutoff, batch)).fetchall()
            if not rows:
                break
            for sid, session_id, idx, ts, data in rows:
                step = from_dict(Step, json.loads(data))
                _trim_step(step)
                self._write("INSERT OR REPLACE INTO steps (id, session_id, idx, ts, data) VALUES (?,?,?,?,?)",
                            (sid, session_id, idx, ts, json.dumps(step.to_dict())))
                trimmed += 1
            since = rows[-1][3]
        self._write("INSERT OR REPLACE INTO meta (key, value) VALUES ('trimmed_until', ?)", (str(cutoff),))
        return trimmed

    # ------------------------------------------------------------------ #
    # deleting a session for good
    # ------------------------------------------------------------------ #
    def _delete(self, table: str, column: str, values: list[str]) -> None:
        # Table and column names are this module's own, never a request's.
        for start in range(0, len(values), 400):
            chunk = values[start:start + 400]
            self._write(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' * len(chunk))})", chunk)

    def delete_session_records(self, thread_id: str) -> dict[str, int]:
        """Forget a chat session: its conversation, its jobs, their clones and
        every step they took, and what was remembered about it."""
        thread = self.threads.pop(thread_id, None)
        if thread is None:
            return {}
        self.thread_by_key.pop(f"{thread.channel}:{thread.key}", None)
        triggers = [t.id for t in self.triggers.values() if t.thread_id == thread_id]
        tasks = [t.id for t in self.tasks.values() if t.thread_id == thread_id]
        clones = [t.worker_id for t in self.tasks.values() if t.thread_id == thread_id and t.worker_id]
        jobs = [j.id for clone in clones for j in self.jobs.of(clone)]
        chat = [m["id"] for m in self.chat if m.get("chat") == thread.key]
        self._tasks_by_thread.pop(thread_id, None)
        self._chat_by_key.pop(thread.key, None)
        memories = [m.id for m in self.memories.values()
                    if m.about == "session" and m.session_key == thread.key]
        findings = [f.id for f in self.findings.values() if f.scope == thread_id]
        for ids, mirror in ((triggers, self.triggers), (tasks, self.tasks), (jobs, self.jobs),
                            (memories, self.memories), (findings, self.findings),
                            (clones, self.sessions), (clones, self.steps)):
            for key in ids:
                mirror.pop(key, None)
        self.chat[:] = [m for m in self.chat if m.get("chat") != thread.key]
        self._delete("threads", "id", [thread_id])
        self._delete("triggers", "id", triggers)
        self._delete("tasks", "id", tasks)
        self._delete("sessions", "id", clones)
        self._delete("steps", "session_id", clones)
        self._delete("contexts", "session_id", clones)
        self._delete("jobs", "id", jobs)
        self._delete("chat", "id", chat)
        self._delete("memories", "id", memories)
        self._delete("findings", "id", findings)
        return {"messages": len(chat), "jobs": len(tasks), "clones": len(clones), "memories": len(memories)}

    # ------------------------------------------------------------------ #
    # long-term memory
    # ------------------------------------------------------------------ #
    def put_memory(self, memory: Memory, *, quiet: bool = False) -> Memory:
        self.memories[memory.id] = memory
        self._write("INSERT OR REPLACE INTO memories (id, ts, data) VALUES (?,?,?)",
                    (memory.id, memory.created_ts, json.dumps(memory.to_dict())))
        if self.bus and not quiet:
            self.bus.emit("memory.changed", memory=memory.to_dict())
        return memory

    def delete_memory(self, memory_id: str) -> Memory | None:
        memory = self.memories.pop(memory_id, None)
        if memory is not None:
            self._write("DELETE FROM memories WHERE id = ?", (memory_id,))
            if self.bus:
                self.bus.emit("memory.changed", memory=memory.to_dict(), deleted=True)
        return memory

    # ------------------------------------------------------------------ #
    # the session notebooks (rimuru/notebook.py)
    # ------------------------------------------------------------------ #
    def put_finding(self, finding: Finding) -> Finding:
        self.findings[finding.id] = finding
        self._write("INSERT OR REPLACE INTO findings (id, ts, scope, data) VALUES (?,?,?,?)",
                    (finding.id, finding.created_ts, finding.scope, json.dumps(finding.to_dict())))
        return finding

    def findings_of(self, scope: str) -> list[Finding]:
        return sorted((f for f in self.findings.values() if f.scope == scope), key=lambda f: f.created_ts)

    # ------------------------------------------------------------------ #
    # live conversations: what a loop holds in mind right now, saved after every
    # step so a restart resumes exactly there (system._recover, AgentLoop.restore)
    # ------------------------------------------------------------------ #
    def save_context(self, session_id: str, data: dict[str, Any]) -> None:
        self._write("INSERT OR REPLACE INTO contexts (session_id, ts, data) VALUES (?,?,?)",
                    (session_id, now(), json.dumps(data, default=str)))

    def load_context(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            if self._closed:
                return None
            row = self._conn.execute("SELECT data FROM contexts WHERE session_id = ?",
                                     (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _check_version(self) -> None:
        """A database from an older Rimuru is backed up before anything changes it;
        one from a newer Rimuru is refused rather than damaged."""
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        found = int(row[0]) if row and str(row[0]).isdigit() else 0
        if found > SCHEMA_VERSION:
            self._conn.close()
            raise RuntimeError(f"This data was made by a newer Rimuru (data version {found}, this one "
                               f"reads up to {SCHEMA_VERSION}). Update Rimuru, or use another --data folder.")
        if found == SCHEMA_VERSION:
            return
        has_data = self._conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchone() is not None
        if has_data:
            from .data_tools import backup

            self._conn.commit()
            kept = backup(self.db_path, f"before-v{SCHEMA_VERSION}")
            log.warning("data version %s -> %s: backed up to %s", found, SCHEMA_VERSION, kept)
        self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                           (str(SCHEMA_VERSION),))
        self._conn.commit()

    def save_inbox(self, session_id: str, items: list[dict[str, Any]]) -> None:
        """What waits for an agent, not yet taken in (agent/loop.py)."""
        self._write("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (f"inbox:{session_id}", json.dumps(items, default=str)))

    def load_inbox(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            if self._closed:
                return []
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?",
                                     (f"inbox:{session_id}",)).fetchone()
        try:
            items = json.loads(row[0]) if row else []
        except ValueError:
            return []
        return [i for i in items if isinstance(i, dict) and i.get("uid") and i.get("text")]

    def save_notes(self, session_id: str, notes: list[str]) -> None:
        self._write("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (f"notes:{session_id}", json.dumps(notes)))

    def load_notes(self, session_id: str) -> list[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (f"notes:{session_id}",)).fetchone()
        return json.loads(row[0]) if row else []

    def context_ids(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT session_id FROM contexts")]

    def drop_context(self, session_id: str) -> None:
        self._write("DELETE FROM contexts WHERE session_id = ?", (session_id,))

    # ------------------------------------------------------------------ #
    # tasks
    # ------------------------------------------------------------------ #
    def _index_task(self, task: Task) -> None:
        if task.thread_id:
            self._tasks_by_thread.setdefault(task.thread_id, []).append(task)

    def tasks_of(self, thread_id: str | None) -> list[Task]:
        """A thread's jobs, oldest first."""
        return self._tasks_by_thread.get(thread_id or "", [])

    def chat_of(self, key: str) -> list[dict[str, Any]]:
        """A session's lines in the chat, oldest first."""
        return self._chat_by_key.get(key, [])

    def put_task(self, task: Task) -> Task:
        if task.id not in self.tasks:
            self._index_task(task)
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
        self.jobs.remember(job)
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
        self.jobs.remember(job)
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

    def _recent_router_steps(self) -> list[Step]:
        """Rimuru's latest turns, re-read only when there are new ones."""
        router = self.sessions.get("rtr_main")
        count = router.step_count if router else 0
        if self._router_recent[0] != count:
            self._router_recent = (count, self.transcript("rtr_main", last=300))
        return self._router_recent[1]

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

        batched = [s.batch_size for s in self._recent_router_steps() if s.batch_size > 0]

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
            "router_steps": router.step_count if (router := self.sessions.get("rtr_main")) else 0,
            "router_avg_batch": (sum(batched) / len(batched)) if batched else 0.0,
            "router_max_batch": max(batched) if batched else 0,
            "spill_size": len(self.router_spill),
        }
