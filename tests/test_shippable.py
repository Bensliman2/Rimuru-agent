"""Shippable (docs/production-plan.md, phase 2): data that survives upgrades and
moves, an optional password, a health check and a problem report without keys."""
from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from rimuru import __version__, data_tools
from rimuru.store import SCHEMA_VERSION, Store
from rimuru.models import Session


def config_for(folder: Path) -> SimpleNamespace:
    return SimpleNamespace(db_path=folder / "rimuru.db", sessions_dir=folder / "sessions",
                           models_path=folder / "models.toml")


def test_an_older_database_is_backed_up_before_anything_changes_it(tmp_path):
    store = Store(tmp_path / "rimuru.db")
    store.put_session(Session(id="wrk_x", kind="worker", title="t", model="m"))
    store._conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    store._conn.commit()
    store.close()
    Store(tmp_path / "rimuru.db").close()
    backups = list((tmp_path / "backups").glob("rimuru-*.db"))
    assert len(backups) == 1
    assert sqlite3.connect(backups[0]).execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    Store(tmp_path / "rimuru.db").close()
    assert len(list((tmp_path / "backups").glob("rimuru-*.db"))) == 1, "only once"


def test_a_new_database_needs_no_backup_and_a_newer_one_is_refused(tmp_path):
    Store(tmp_path / "rimuru.db").close()
    assert not (tmp_path / "backups").exists()
    db = sqlite3.connect(tmp_path / "rimuru.db")
    db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),))
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="newer Rimuru"):
        Store(tmp_path / "rimuru.db")


def test_export_and_import_move_everything_but_keys(tmp_path):
    old = config_for(tmp_path / "old")
    store = Store(old.db_path)
    store.put_session(Session(id="wrk_y", kind="worker", title="kept", model="m"))
    store.close()
    (old.sessions_dir / "site").mkdir(parents=True)
    (old.sessions_dir / "site" / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (old.sessions_dir / "site" / ".env").write_text("TOKEN=secret-value-123", encoding="utf-8")
    old.models_path.write_text('[roles]\nrouter = "a"\n', encoding="utf-8")

    archive = tmp_path / "export.zip"
    assert data_tools.export(old, archive) == {"files": 1}
    names = zipfile.ZipFile(archive).namelist()
    assert "sessions/site/index.html" in names and not any(".env" in n for n in names)

    new = config_for(tmp_path / "new")
    assert data_tools.import_(new, archive) == {"files": 1}
    assert (new.sessions_dir / "site" / "index.html").read_text(encoding="utf-8") == "<h1>hi</h1>"
    moved = Store(new.db_path)
    assert moved.sessions["wrk_y"].title == "kept"
    moved.close()
    with pytest.raises(ValueError, match="already has data"):
        data_tools.import_(new, archive)


def test_an_import_never_writes_outside_its_folder(tmp_path):
    archive = tmp_path / "crafted.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("rimuru.db", b"")
        z.writestr("sessions/../../escaped.txt", "x")
    data_tools.import_(config_for(tmp_path / "in"), archive)
    assert not (tmp_path / "escaped.txt").exists()


def test_wipe_deletes_everything(tmp_path):
    config = config_for(tmp_path)
    Store(config.db_path).close()
    (config.sessions_dir / "a").mkdir(parents=True)
    (tmp_path / "rimuru.log").write_text("x", encoding="utf-8")
    data_tools.wipe(config)
    assert not config.db_path.exists() and not config.sessions_dir.exists() and not (tmp_path / "rimuru.log").exists()


@pytest.fixture
def app(tmp_path, monkeypatch):
    from rimuru.web import app as web_app

    config = web_app.CONFIG
    saved = (config.db_path, config.workspace, config.sessions_dir, config.provider)
    config.db_path, config.workspace = tmp_path / "data" / "t.db", tmp_path / "ws"
    config.sessions_dir, config.provider = tmp_path / "data" / "sessions", "mock"
    monkeypatch.setenv("RIMURU_PASSWORD", "correct horse")
    try:
        with TestClient(web_app.create_app(speed=50), base_url="http://127.0.0.1:8800") as client:
            yield client
    finally:
        config.db_path, config.workspace, config.sessions_dir, config.provider = saved


def test_with_a_password_everything_asks_for_it(app):
    assert app.get("/api/overview").status_code == 401
    page = app.get("/", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login"
    assert app.get("/api/health").json()["version"] == __version__, "health stays open"
    wrong = app.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert wrong.status_code == 401 and "not the password" in wrong.text

    right = app.post("/login", data={"password": "correct horse"}, follow_redirects=False)
    assert right.status_code == 303
    cookie = right.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert app.get("/api/overview").status_code == 200, "signed in now"
    app.get("/logout")
    assert app.get("/api/overview").status_code == 401


def test_guessing_is_slowed_down(app):
    for _ in range(10):
        app.post("/login", data={"password": "nope"})
    blocked = app.post("/login", data={"password": "correct horse"})
    assert blocked.status_code == 429


def test_the_problem_report_has_the_log_and_no_keys(app, tmp_path):
    app.post("/login", data={"password": "correct horse"})
    (tmp_path / "data" / "rimuru.log").write_text("boom with sk-abcdefghijklmnopqrstuvwxyz012345\n", encoding="utf-8")
    answer = app.get("/api/report")
    assert answer.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(answer.content))
    text = archive.read("rimuru.log").decode("utf-8")
    assert "boom" in text and "sk-abcdefghijklmnopqrstuvwxyz012345" not in text
    assert __version__ in archive.read("about.json").decode("utf-8")
