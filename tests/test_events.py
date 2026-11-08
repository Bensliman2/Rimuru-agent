"""The story the pages show - what came in, what Rimuru decided and said, what the
watchdog did - is still there after a restart, stays within a limit, and goes
when its session is deleted. Offline: the demo model."""
from __future__ import annotations

import json
import time

from rimuru.bus import STORY, Bus, compact
from rimuru.config import Config
from rimuru.store import Store
from rimuru.system import RimuruSystem


def app_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "data" / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "data" / "sessions", "models_path": tmp_path / "m.toml",
                        "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    return TestClient(create_app(speed=50))


def wait_for(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


def story(system) -> list[dict]:
    return system.bus.recent(2000, topics=set(STORY))


def test_the_story_survives_a_restart_and_goes_with_its_session(tmp_path, monkeypatch, steady_demo):
    with app_client(tmp_path, monkeypatch) as client:
        system = client.app.state.system
        client.post("/api/message", json={"text": "Build a small page about cats", "thread_key": "s-cats0001"})
        client.post("/api/message", json={"text": "Write a haiku about dogs", "thread_key": "s-dogs0001"})
        assert wait_for(lambda: sum(t.status == "done" for t in system.store.tasks.values()) == 2)
        assert wait_for(lambda: sum(e["topic"] == "channel.sent" for e in story(system)) >= 4)
        cats = next(t for t in system.store.threads.values() if t.key == "s-cats0001")
        clone = next(t.worker_id for t in system.store.tasks.values() if t.thread_id == cats.id)

    with app_client(tmp_path, monkeypatch) as client:      # the same data, a fresh start
        system = client.app.state.system
        before = story(system)
        topics = {e["topic"] for e in before}
        assert {"channel.sent", "worker.finished", "tool.called", "filter.decided"} <= topics
        assert any("cats" in str(e) for e in before)
        assert any(clone in str(e) for e in before), "a clone's own events are in the story"

        assert client.delete("/api/chats/s-cats0001").json()["ok"] is True
        assert not [e for e in story(system) if clone in str(e) or cats.id in str(e)]

    with app_client(tmp_path, monkeypatch) as client:      # and after another restart, still gone
        after = story(client.app.state.system)
        assert after and not [e for e in after if clone in str(e) or cats.id in str(e)]
        assert any("dogs" in str(e) for e in after), "another session's story was touched"


def test_only_the_story_is_saved_compactly_and_within_a_limit(tmp_path):
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    system = RimuruSystem(config, speed=50.0)
    store = system.store

    system.bus.emit("session.updated", session_id="x")                     # not part of the story
    assert store.recent_events(10) == []
    system.bus.emit("tool.called", session_id="wrk_1", tool="write_file",
                    args={"path": "a.html", "content": "x" * 50_000})
    saved = store.recent_events(10)[0]
    assert saved["args"]["path"] == "a.html" and len(saved["args"]["content"]) == 300, "a whole file is not kept"
    store.close()

    bounded = Store(tmp_path / "other.db", bus=Bus())
    for i in range(450):
        bounded.add_event({"ts": float(i), "topic": "channel.sent", "text": f"m{i}"}, keep=100)
    rows = bounded._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert rows <= 100 + 200, "trimmed as it goes"
    assert bounded.recent_events(1)[0]["text"] == "m449"
    bounded.close()


def test_a_restored_event_keeps_the_numbering_going():
    bus = Bus()
    bus.restore([{"seq": 41, "topic": "channel.sent", "ts": 1.0}])
    assert bus.emit("channel.sent")["seq"] == 42
    assert json.dumps(compact({"topic": "t", "ts": 1.0, "text": "y" * 5000})).count("y") == 1000
