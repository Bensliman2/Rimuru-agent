"""How does Rimuru hold up as its history grows? (python run.py scale)

Builds a made-up database of a given size, then times what a restart costs and
what each page refresh costs - through the real app, not an estimate.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import tracemalloc
from pathlib import Path

from .ids import new_id
from .models import Session, Step, Task, Thread, Trigger
from .store import SCHEMA, Store


def build(path: Path, sessions: int, jobs_per: int, steps_per: int) -> dict[str, int]:
    """A database with `sessions` chat sessions of `jobs_per` jobs of `steps_per` steps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    start = time.time() - 86400
    text = ("<div class='card'>hello</div>\n" * 40)[:1200]
    rows: dict[str, list[tuple]] = {k: [] for k in ("threads", "tasks", "sessions", "steps", "chat", "triggers")}
    for s in range(sessions):
        at = start + s * 60
        thread = Thread(id=new_id("thread"), channel="console", key=f"s-{s:08x}", title=f"Session {s}",
                        created_ts=at, last_ts=at)
        rows["threads"].append((thread.id, thread.channel, thread.key, thread.title, at, at, json.dumps(thread.to_dict())))
        for c in range(20):
            line = {"id": new_id("step"), "ts": at + c, "role": "user" if c % 2 == 0 else "rimuru",
                    "text": f"message {c} " * 8, "chat": thread.key}
            rows["chat"].append((line["id"], line["ts"], line["role"], line["text"], json.dumps(line)))
        for j in range(jobs_per):
            trigger = Trigger(id=new_id("trigger"), channel="console", thread_key=thread.key, author="you",
                              kind="human_direct", content="build me something " * 5, thread_id=thread.id,
                              verdict="accepted", route="router", ts=at + j)
            rows["triggers"].append((trigger.id, trigger.ts, trigger.channel, trigger.thread_key, trigger.thread_id,
                                     trigger.author, trigger.kind, trigger.content, trigger.verdict, "", trigger.route,
                                     json.dumps(trigger.to_dict())))
            worker = Session(id=new_id("worker"), kind="worker", title=f"Job {j}", model="m", status="done",
                             name="Mika", persona="quick", created_ts=at + j, updated_ts=at + j, step_count=steps_per)
            task = Task(id=new_id("task"), title=f"Job {j} of session {s}", brief="do it " * 40, thread_id=thread.id,
                        worker_id=worker.id, status="done", result="Done. " * 30, origin_trigger_id=trigger.id,
                        created_ts=at + j, started_ts=at + j, finished_ts=at + j + 600)
            worker.task_id = task.id
            rows["sessions"].append((worker.id, worker.kind, worker.title, worker.model, worker.status, task.id,
                                     at + j, at + j, json.dumps(worker.to_dict())))
            rows["tasks"].append((task.id, task.title, task.status, worker.id, at + j, at + j, at + j + 600,
                                  json.dumps(task.to_dict())))
            for i in range(steps_per):
                call = ({"id": "c", "name": "write_file", "args": {"path": f"page{i}.html", "content": text}}
                        if i % 3 == 0 else {"id": "c", "name": "read_file", "args": {"path": f"page{i}.html"}})
                step = Step(id=new_id("step"), session_id=worker.id, index=i, ts=at + j + i,
                            injected="result of the last tool " * 40, text="next I will " * 10,
                            tool_calls=[call], tool_results=[{"name": call["name"], "output": "ok " * 200}])
                rows["steps"].append((step.id, step.session_id, step.index, step.ts, json.dumps(step.to_dict())))
    conn.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?)", rows["threads"])
    conn.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", rows["sessions"])
    conn.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)", rows["tasks"])
    conn.executemany("INSERT INTO steps VALUES (?,?,?,?,?)", rows["steps"])
    conn.executemany("INSERT INTO chat VALUES (?,?,?,?,?)", rows["chat"])
    conn.executemany("INSERT INTO triggers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows["triggers"])
    conn.commit()
    conn.close()
    return {k: len(v) for k, v in rows.items()}


def measure_store(path: Path) -> dict[str, float]:
    """A restart: how long the store takes to load, and the memory it then holds
    (two loads: the memory tracer slows the one it watches)."""
    started = time.perf_counter()
    Store(path).close()
    seconds = time.perf_counter() - started
    tracemalloc.start()
    store = Store(path)
    _now, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    store.close()
    return {"restart_s": seconds, "memory_mb": peak / 1e6}


def measure_pages(path: Path, tmp: Path) -> dict[str, float]:
    """What one refresh of each page costs, with what a browser would ask for
    (the second time: the first also works out and saves each clone's summary)."""
    from fastapi.testclient import TestClient

    from .config import CONFIG
    from .web.app import create_app

    CONFIG.db_path, CONFIG.workspace = path, tmp / "ws"
    CONFIG.sessions_dir, CONFIG.models_path, CONFIG.skills_dir = tmp / "sessions", tmp / "m.toml", tmp / "skills"
    CONFIG.demo, CONFIG.setup = True, None
    out: dict[str, float] = {}
    with TestClient(create_app(speed=50)) as client:
        hood = ["/api/overview", "/api/sessions", "/api/work?limit=60", "/api/threads?limit=13", "/api/batching"]
        chat = ["/api/chats?limit=60", "/api/work?chat=s-00000000", "/api/chat?chat=s-00000000"]
        for name, urls in (("hood_ms", hood), ("chat_ms", chat)):
            for _ in range(2):
                started = time.perf_counter()
                for url in urls:
                    assert client.get(url).status_code == 200, url
                out[name] = (time.perf_counter() - started) * 1000
    return out


def main() -> int:
    print("  jobs      steps   database |  restart   memory | Under the hood   chat page  (one refresh)")
    for sessions, jobs, steps in ((10, 10, 30), (40, 10, 30), (100, 10, 30), (500, 10, 30)):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            tmp = Path(folder)
            path = tmp / "probe.db"
            counts = build(path, sessions, jobs, steps)
            size = path.stat().st_size / 1e6
            store = measure_store(path)
            pages = measure_pages(path, tmp)
        print(f"{counts['tasks']:>6} {counts['steps']:>10} {size:>8.0f} MB | {store['restart_s']:>6.2f} s "
              f"{store['memory_mb']:>6.0f} MB | {pages['hood_ms']:>11.0f} ms {pages['chat_ms']:>8.0f} ms", flush=True)
    return 0
