"""What goes wrong quietly is written down, keys taken out - and if the database
cannot save, the person is told."""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import pytest

from rimuru import logs
from rimuru.bus import Bus
from rimuru.store import Store

KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture
def log_file(tmp_path):
    path = logs.setup(tmp_path)
    yield path
    for handler in list(logs.log.handlers):
        handler.close()
        logs.log.removeHandler(handler)


def written(path) -> str:
    for handler in logs.log.handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_a_small_failure_carries_on_and_is_written_down(log_file):
    with logs.noted("the housekeeping"):
        raise ValueError("nope")
    text = written(log_file)
    assert "the housekeeping failed" in text and "ValueError: nope" in text


def test_keys_never_reach_the_log(log_file):
    with logs.noted("calling the provider"):
        raise RuntimeError(f"refused: {KEY}")
    logs.log.warning("the key was %s", KEY)
    text = written(log_file)
    assert "refused" in text and KEY not in text


def test_stopping_still_stops():
    async def run():
        with logs.noted("this"):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


def test_a_broken_listener_is_written_down_and_the_others_still_run(caplog):
    bus = Bus()
    heard: list[str] = []

    def broken(_event):
        raise ValueError("listener bug")

    bus.on("x.y", broken)
    bus.on("x.y", lambda event: heard.append(event["topic"]))
    with caplog.at_level(logging.WARNING, logger="rimuru.bus"):
        bus.emit("x.y")
    assert heard == ["x.y"]
    assert "a listener for x.y failed" in caplog.text


def test_when_the_database_cannot_save_the_person_is_told(tmp_path, caplog):
    store = Store(tmp_path / "t.db")
    assert store.save_problem == ""
    real = store._conn

    class Full:
        def execute(self, *_a):
            raise sqlite3.OperationalError("database or disk is full")

    store._conn = Full()
    with caplog.at_level(logging.ERROR, logger="rimuru.store"):
        store.set_setting("a", "1")
        store.set_setting("a", "2")
    assert "the disk is full" in store.save_problem and "would be lost" in store.save_problem
    assert caplog.text.count("could not write to the database") == 1, "once, not on every write"

    store._conn = real
    store.set_setting("a", "3")
    assert store.save_problem == "", "it says so only while it is true"
    store.close()
