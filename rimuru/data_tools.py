"""The person's data: backed up before an upgrade, exported, imported, wiped.

    python run.py export rimuru-backup.zip     everything, in one file (no API keys)
    python run.py import rimuru-backup.zip     into an empty data folder
    python run.py wipe                         delete everything, after typing it out

What is in it: the database (chats, jobs, memories), the sessions' folders of
files, and which models do which job. Never the API keys: they stay in .env.
"""
from __future__ import annotations

import io
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any

from .safety import secret_file

KEEP_BACKUPS = 5


def backup(db_path: Path, reason: str) -> Path:
    """A consistent copy of the database in data/backups, the last few kept."""
    db_path = Path(db_path)
    folder = db_path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"rimuru-{time.strftime('%Y%m%d-%H%M%S')}-{reason}.db"
    source = sqlite3.connect(db_path)
    try:
        copy = sqlite3.connect(target)
        with copy:
            source.backup(copy)
        copy.close()
    finally:
        source.close()
    for old in sorted(folder.glob("rimuru-*.db"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return target


def _files(folder: Path):
    skip = {"node_modules", "__pycache__", ".git", ".venv", "venv"}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if path.is_file() and not any(p in skip for p in rel.parts) and not secret_file(rel.as_posix()):
            yield path, rel.as_posix()


def export(config: Any, target: Path) -> dict[str, int]:
    """Everything worth keeping, in one zip. Keys and files that hold them stay out."""
    target = Path(target)
    counts = {"files": 0}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        snapshot = io.BytesIO()
        tmp = target.with_suffix(".db.tmp")
        source = sqlite3.connect(config.db_path)
        try:
            copy = sqlite3.connect(tmp)
            with copy:
                source.backup(copy)
            copy.close()
        finally:
            source.close()
        archive.write(tmp, "rimuru.db")
        tmp.unlink()
        del snapshot
        sessions = Path(config.sessions_dir)
        if sessions.is_dir():
            for path, rel in _files(sessions):
                archive.write(path, f"sessions/{rel}")
                counts["files"] += 1
        if Path(config.models_path).is_file():
            archive.write(config.models_path, "models.toml")
        archive.writestr("README.txt", "A Rimuru export: python run.py import <this file>\n"
                                       "API keys are not in it: add them again with the Models button.\n")
    return counts


def import_(config: Any, source: Path) -> dict[str, int]:
    """Into an empty data folder only: an import never overwrites what is there."""
    db = Path(config.db_path)
    if db.exists() and db.stat().st_size > 0:
        raise ValueError(f"{db} already has data. Import into a new data folder (--data), "
                         f"or export and wipe this one first.")
    counts = {"files": 0}
    sessions = Path(config.sessions_dir).resolve()
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if "rimuru.db" not in names:
            raise ValueError("That file is not a Rimuru export (no rimuru.db in it).")
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(archive.read("rimuru.db"))
        for name in names:
            if not name.startswith("sessions/") or name.endswith("/"):
                continue
            target = (sessions / name[len("sessions/"):]).resolve()
            if not target.is_relative_to(sessions):
                continue                      # a crafted path never escapes
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
            counts["files"] += 1
        if "models.toml" in names and not Path(config.models_path).exists():
            Path(config.models_path).parent.mkdir(parents=True, exist_ok=True)
            Path(config.models_path).write_bytes(archive.read("models.toml"))
    return counts


def wipe(config: Any) -> list[str]:
    """Delete the database, its backups, the sessions' folders and the log."""
    gone = []
    data = Path(config.db_path).parent
    for path in [Path(config.db_path), *data.glob(Path(config.db_path).name + "-*")]:
        if path.exists():
            path.unlink()
            gone.append(str(path))
    for folder in (Path(config.sessions_dir), data / "backups", data / "deps"):
        if folder.exists():
            shutil.rmtree(folder)
            gone.append(str(folder))
    for log in data.glob("rimuru.log*"):
        log.unlink(missing_ok=True)
        gone.append(str(log))
    return gone
