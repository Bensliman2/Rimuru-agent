"""The person's folders: browsing them to pick one for a session, and opening one.

Rimuru runs on the person's own computer - the app answers only local requests
(web/app.py, local_only) - so it can show their folders and open one in File
Explorer or Finder. It lists folder names, never file contents. And it refuses a
session folder whose loss would really hurt: a whole drive, the whole home
folder, the system's own folders, or Rimuru's folder (it holds the API keys).
"""
from __future__ import annotations

import os
import stat
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import ROOT

# Never worth showing to a person picking a project folder.
_SKIP = {"node_modules", "__pycache__", "venv", ".venv", "appdata", "$recycle.bin",
         "system volume information", "windows", "program files", "program files (x86)",
         "programdata", "recovery", "perflogs", "config.msi", "msocache"}
_BAD_CHARS = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _system_dirs() -> list[Path]:
    names = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
    found = [Path(v) for v in (os.environ.get(n) for n in names) if v]
    if os.name != "nt":
        found += [Path(p) for p in ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc",
                                    "/sbin", "/sys", "/usr", "/var", "/System", "/Library",
                                    "/Applications", "/private")]
    return [p.resolve() for p in found if p.exists()]


def problem(path: Path, config: Any) -> str | None:
    """Why a session may not work in this folder, or None when it may."""
    path = Path(path).resolve()
    if path.parent == path:
        return "That is a whole drive. Pick a folder inside it."
    if path == Path.home().resolve():
        return "That is your whole home folder. Pick or make a folder inside it."
    if any(path == s or _inside(path, s) for s in _system_dirs()):
        return "That is one of the computer's own system folders. Pick a folder of yours."
    if path == Path(config.sessions_dir).resolve():
        # Every session's own folder is in here: one session must not reach the others.
        return "That folder holds every session's own folder. Pick one inside it, or make a new one."
    root = ROOT.resolve()
    if _inside(root, path):
        return "That folder holds Rimuru itself, with its keys. Pick another one."
    data = Path(config.db_path).resolve().parent
    if _inside(data, path) and not _inside(path, Path(config.sessions_dir).resolve()):
        return "That folder holds Rimuru's records. Pick another one."
    if _inside(path, root) and not _inside(path, Path(config.sessions_dir).resolve()):
        return "That is part of Rimuru itself. Pick a folder of yours."
    return None


def check(raw: str, config: Any) -> Path:
    """A folder the person picked or typed, ready to use; ValueError says why not."""
    text = str(raw or "").strip().strip('"').strip()
    if not text:
        raise ValueError("Pick a folder first.")
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not path.is_absolute():
        raise ValueError(r"Give the folder's full path, like C:\Users\you\Documents\my-site.")
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("There is no folder there.") from None
    if not path.is_dir():
        raise ValueError("That is a file, not a folder.")
    why = problem(path, config)
    if why:
        raise ValueError(why)
    return path


def places() -> list[dict[str, str]]:
    """Where a person usually starts looking."""
    home = Path.home()
    out = [{"label": "Home", "path": str(home)}]
    for name in ("Desktop", "Documents", "Downloads"):
        for base in (home, home / "OneDrive"):
            if (base / name).is_dir():
                out.append({"label": name, "path": str(base / name)})
                break
    if os.name == "nt":
        lister = getattr(os, "listdrives", None)
        drives = lister() if lister else [f"{c}:\\" for c in string.ascii_uppercase[2:]
                                          if os.path.exists(f"{c}:\\")]
        out += [{"label": d.rstrip("\\"), "path": d} for d in drives if not d.upper().startswith(("A:", "B:"))]
    else:
        out.append({"label": "Computer", "path": "/"})
    # In Docker (docker-compose.yml) projects are shared with Rimuru here.
    projects = Path(os.environ.get("RIMURU_PROJECTS", "/projects"))
    if os.name != "nt" and projects.is_dir():
        out.insert(1, {"label": "Projects", "path": str(projects)})
    return out


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith((".", "$", "~")) or entry.name.lower() in _SKIP:
        return True
    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM)) if attrs else False


def listing(raw: str | None, config: Any, cap: int = 400) -> dict[str, Any]:
    """The folders inside one folder - names only - and whether it can be used."""
    path = Path(os.path.expanduser(str(raw).strip())) if raw and str(raw).strip() else Path.home()
    try:
        path = path.resolve(strict=True)
        if not path.is_dir():
            path = path.parent
    except (OSError, RuntimeError):
        path = Path.home().resolve()
    dirs: list[dict[str, str]] = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_dir() or _hidden(entry):
                continue
        except OSError:
            continue
        dirs.append({"name": entry.name, "path": entry.path})
        if len(dirs) >= cap:
            break
    why = problem(path, config)
    parent = path.parent if path.parent != path else None
    return {"path": str(path), "name": path.name or str(path),
            "parent": str(parent) if parent else None, "dirs": dirs, "places": places(),
            "usable": why is None, "why_not": why or ""}


def make(parent_raw: str, name: str, config: Any) -> Path:
    """A new folder inside parent, for a session to work in."""
    name = " ".join(str(name or "").split()).strip(". ")
    if not name:
        raise ValueError("Give the new folder a name.")
    if len(name) > 80 or any(c in _BAD_CHARS for c in name) or name.upper() in _RESERVED:
        raise ValueError('A folder name cannot use \\ / : * ? " < > | and must be short.')
    try:
        parent = Path(os.path.expanduser(str(parent_raw or "").strip())).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("There is no folder there.") from None
    if not parent.is_dir():
        raise ValueError("That is a file, not a folder.")
    path = parent / name
    why = problem(path, config)
    if why:
        raise ValueError(why)
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError("A folder with that name is already there.") from None
    except OSError as exc:
        raise ValueError(f"Could not make it: {exc.strerror or exc}") from None
    return path


def open_in_file_manager(path: Path) -> None:
    """Show a folder in File Explorer, Finder or the desktop's file manager."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - a folder Rimuru chose, never a request's path
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607
