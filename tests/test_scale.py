"""Rimuru does not slow down as its history grows: a restart does not load every
step, a finished clone's card never needs its steps again, every list comes a
page at a time, and steps do not keep whole files. Offline."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from rimuru import scale_probe
from rimuru.ids import new_id
from rimuru.models import Job, Session, Step
from rimuru.store import Store
from rimuru.web import friendly

CONV = frozenset({"console", "telegram"})


@pytest.fixture(scope="module")
def big(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("big") / "probe.db"
    scale_probe.build(path, sessions=30, jobs_per=10, steps_per=30)   # 300 jobs, 9,000 steps
    return path


def client_for(tmp_path, monkeypatch, db):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    for name, value in {"db_path": db, "workspace": tmp_path / "ws", "sessions_dir": tmp_path / "sessions",
                        "models_path": tmp_path / "m.toml", "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    return TestClient(create_app(speed=50))


def test_a_restart_does_not_load_the_history(big):
    started = time.perf_counter()
    store = Store(big)
    seconds = time.perf_counter() - started
    assert store.steps.peek(next(iter(store.sessions))) is None and not store.steps._lists
    assert len(store.sessions) == 300 and seconds < 1.5
    one = next(iter(store.sessions))
    assert len(store.transcript(one)) == 30, "still all there, read when wanted"
    assert [s.index for s in store.transcript(one, last=3)] == [27, 28, 29]
    store.close()


def test_only_the_recent_steps_stay_in_memory(big):
    store = Store(big)
    for session in store.sessions:
        store.transcript(session)
    assert len(store.steps._lists) == 64
    store.close()


def test_a_finished_clones_card_never_needs_its_steps_again(big):
    store = Store(big)
    worker = next(iter(store.sessions.values()))
    reads = []
    original = store._load_steps
    store.steps._load = lambda sid: reads.append(sid) or original(sid)
    first = friendly.worker_card(store, worker, CONV)
    assert reads == [worker.id] and worker.summary["steps"] == 30
    store.steps.pop(worker.id)
    assert friendly.worker_card(store, worker, CONV) == {**first, "elapsed": first["elapsed"]}
    assert reads == [worker.id], "worked out once"
    store.close()

    again = Store(big)                       # and it is saved: not after a restart either
    reads.clear()
    original2 = again._load_steps
    again.steps._load = lambda sid: reads.append(sid) or original2(sid)
    assert friendly.worker_card(again, again.sessions[worker.id], CONV)["turns"] == 30 and reads == []
    again.close()


def test_every_list_comes_a_page_at_a_time(big, tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch, big) as client:
        work = client.get("/api/work", params={"limit": 20}).json()
        assert len(work["cards"]) == 20 and work["total"] == 300 and work["done"] == 300
        chats = client.get("/api/chats", params={"limit": 10}).json()
        assert len(chats["items"]) == 10 and chats["total"] == 30
        oldest = "s-00000000"
        assert oldest not in [c["key"] for c in chats["items"]]
        also = client.get("/api/chats", params={"limit": 10, "also": oldest}).json()
        assert oldest in [c["key"] for c in also["items"]], "the session that is open stays listed"
        assert len(client.get("/api/sessions", params={"limit": 25}).json()["items"]) == 25
        assert len(client.get("/api/threads", params={"limit": 5}).json()["items"]) == 5
        memory = client.get("/api/memory", params={"limit": 4}).json()
        assert len(memory["sessions"]) == 4 and memory["total"] == 30
        session_work = client.get("/api/work", params={"chat": oldest}).json()
        assert len(session_work["cards"]) == 10, "a session's own clones: all of them"
        clone = work["cards"][0]["id"]
        detail = client.get(f"/api/sessions/{clone}", params={"limit": 5}).json()
        assert len(detail["transcript"]) == 5 and detail["total"] == 30
        o = client.get("/api/overview").json()
        assert o["usage_by_kind"]["worker"]["output_tokens"] == 0 and o["filter"]["seen"] == 300


def test_clones_at_work_are_always_listed(big, tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch, big) as client:
        store = client.app.state.system.store
        oldest = min(store.sessions_of_kind("worker"), key=lambda s: s.created_ts)
        status, oldest.status = oldest.status, "thinking"
        cards = client.get("/api/work", params={"limit": 3}).json()["cards"]
        oldest.status = status
        assert len(cards) == 4 and oldest.id in [c["id"] for c in cards]


def test_a_step_keeps_the_start_of_a_file_not_all_of_it(tmp_path):
    store = Store(tmp_path / "t.db")
    worker = store.put_session(Session(id=new_id("worker"), kind="worker", title="t", model="m"))
    content = "line of the page\n" * 3000
    args = {"path": "page.html", "content": content}
    store.add_step(Step(id=new_id("step"), session_id=worker.id, index=0,
                        tool_calls=[{"id": "c", "name": "write_file", "args": args}]))
    assert args["content"] == content, "the conversation with the model is left alone"
    stored = store._conn.execute("SELECT data FROM steps").fetchone()[0]
    assert len(stored) < 3000 < len(content)
    step = store.transcript(worker.id)[0]
    assert friendly.describe_call("write_file", step.tool_calls[0]["args"]) == "Wrote page.html (3000 lines)"
    assert worker.step_count == 1
    store.close()


def test_old_steps_are_trimmed_and_keep_what_the_pages_need(tmp_path):
    store = Store(tmp_path / "t.db")
    worker = store.put_session(Session(id=new_id("worker"), kind="worker", title="t", model="m", status="done"))
    old_ts = time.time() - 40 * 86400
    output = "noise\n" * 2000 + "===== 44 passed in 1.2s ====="
    store.add_step(Step(id=new_id("step"), session_id=worker.id, index=0, ts=old_ts, injected="x" * 9000,
                        thinking="y" * 9000, text="z" * 9000,
                        tool_calls=[{"id": "a", "name": "write_file", "args": {"path": "a.py", "content": "c"}},
                                    {"id": "b", "name": "bash", "args": {"command": "pytest"}}],
                        tool_results=[{"name": "write_file", "output": "ok"}, {"name": "bash", "output": output}]))
    store.add_step(Step(id=new_id("step"), session_id=worker.id, index=1, injected="q" * 9000))
    assert store.trim_old_details(30) == 1
    assert store.trim_old_details(30) == 0, "only what was not trimmed before"
    assert store.trim_old_details(0) == 0, "0 keeps everything"
    store.steps.pop(worker.id)
    old, new = store.transcript(worker.id)
    assert len(old.injected) == 400 and old.thinking == "" and len(new.injected) == 9000
    card = friendly.worker_card(store, worker, CONV)
    assert card["files"] == ["a.py"] and "44 passed" in (card["tests"] or "")
    store.close()


def test_a_finished_command_is_read_when_wanted_and_a_running_one_stays(tmp_path):
    store = Store(tmp_path / "t.db")
    running = store.put_job(Job(id="job_run", session_id="w1", command="sleep 9"))
    done = store.put_job(Job(id="job_old", session_id="w1", command="ls", status="done", result="a.txt"))
    assert store.jobs.get("job_run") is running, "the very object its clone is updating"
    assert store.jobs.get("job_old") is not done and store.jobs.get("job_old").result == "a.txt"
    assert {j.id for j in store.jobs.of("w1")} == {"job_run", "job_old"} and store.jobs
    store.update_job(running, status="done", result="ok")
    assert not store.jobs.running and store.jobs.get("job_run").result == "ok"
    assert store.jobs.get("nope") is None
    store.close()

    again = Store(tmp_path / "t.db")
    assert not again.jobs.running and len(again.jobs) == 2
    again.close()


def test_how_long_old_details_are_kept_is_the_persons_choice(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch, tmp_path / "t.db") as client:
        assert client.get("/api/settings/details").json()["days"] == 30
        assert client.post("/api/settings/details", json={"days": 90}).json()["days"] == 90
        assert client.app.state.system.keep_details_days() == 90
        assert client.post("/api/settings/details", json={"days": "soon"}).status_code == 400
        assert client.post("/api/settings/details", json={"days": -1}).status_code == 400
    with client_for(tmp_path, monkeypatch, tmp_path / "t.db") as client:
        assert client.get("/api/settings/details").json()["days"] == 90, "kept across restarts"
