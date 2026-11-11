"""What a clone built, opened in the browser - on a port of its own.

The app's pages and controls answer on one port (8800). Whatever a clone built
is served from another (8820 by default, RIMURU_VIEW_PORT), which browsers treat
as a different website: a page that someone's job produced cannot call the
app's controls, read its answers or use its live feed. Each session's page sees
only that session's folder, and files that hold keys, hidden files and
dependency folders are never served.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .. import sessions as chats
from ..logs import log
from ..safety import secret_file
from .shared import Guard, Web

# Never served, at any depth: dependencies and tool folders.
SKIP = {"node_modules", "__pycache__", ".git", ".venv", "venv", "site-packages"}
PAGES = (".html", ".htm")


def servable(rel: str) -> bool:
    """A file a built page may load: inside the folder, not hidden, holding no keys."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    return bool(parts) and not any(p.startswith(".") or p in SKIP for p in parts) and not secret_file(rel)


def view_path(files: list[str]) -> str:
    """The page to open among the files a job made: index.html at the top if there
    is one, else the shallowest page. "" when it made no page."""
    pages = [re.sub(r"^(\./)+", "", f.replace("\\", "/")) for f in files if f.lower().endswith(PAGES)]
    pages = [p for p in pages if servable(p)]
    if not pages:
        return ""
    return min(pages, key=lambda p: (p.count("/"), not p.lower().endswith(("index.html", "index.htm")), p))


MAX_ZIP = 200 * 1024 * 1024


def zip_folder(folder: Path) -> bytes:
    """A session's files as one zip: what view would serve, nothing that holds keys."""
    import io
    import zipfile

    buffer, total = io.BytesIO(), 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, dirs, files in os.walk(folder):
            rel_dir = Path(path).relative_to(folder).as_posix()
            dirs[:] = [d for d in dirs if servable(d if rel_dir == "." else f"{rel_dir}/{d}")]
            for name in files:
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                if not servable(rel):
                    continue
                total += (Path(path) / name).stat().st_size
                if total > MAX_ZIP:
                    raise ValueError("This folder is too big to download as one file (over 200 MB).")
                archive.write(Path(path) / name, rel)
    return buffer.getvalue()


def _listing(key: str, rel: str, folder: Path) -> str:
    rows = []
    for item in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        inner = f"{rel}/{item.name}" if rel else item.name
        if not servable(inner):
            continue
        name = html.escape(item.name + ("/" if item.is_dir() else ""))
        rows.append(f'<li><a href="/{key}/{html.escape(inner)}{"/" if item.is_dir() else ""}">{name}</a></li>')
    title = html.escape(rel or "Files")
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f"<title>{title}</title><style>body{{font:15px/1.6 system-ui,sans-serif;margin:32px;"
            f"color:#0F1B2D}}a{{color:#1B6A9A}}</style><h1>{title}</h1>"
            f"<ul>{''.join(rows) or '<li>Nothing to show here yet.</li>'}</ul>")


def create_viewer(web: Web, auth: Any = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    guard = Guard(web.system.config)

    @app.middleware("http")
    async def local_only(request, call_next):
        refused = guard.refuse(request.headers, request.method,
                               request.client.host if request.client else None)
        if refused:
            return JSONResponse({"error": refused}, status_code=403)
        # With a password, the same sign-in as the app (cookies are per host).
        if auth is not None and not auth.valid(request.cookies.get("rimuru_session")):
            return JSONResponse({"error": "Sign in to Rimuru first."}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def missing() -> HTMLResponse:
        return HTMLResponse("<!doctype html><title>Not here</title><p>There is nothing to show at this address.",
                            status_code=404)

    @app.get("/{key}")
    async def session_root(key: str):
        return RedirectResponse(f"/{key}/")

    @app.get("/{key}/{path:path}")
    async def show(key: str, path: str = ""):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key):
            return missing()
        thread = web.session_thread(key)
        if thread is None or chats.folder_kind(thread) == "remote":
            return missing()
        root = chats.folder_for(web.system.config, thread).resolve()
        target = (root / path).resolve()
        try:
            rel = target.relative_to(root).as_posix()
        except ValueError:
            return missing()
        rel = "" if rel == "." else rel
        if rel and not servable(rel):
            return missing()
        if target.is_dir():
            if path and not path.endswith("/"):
                return RedirectResponse(f"/{key}/{path}/")
            index = next((target / n for n in ("index.html", "index.htm") if (target / n).is_file()), None)
            if index is None:
                return HTMLResponse(await asyncio.to_thread(_listing, key, rel, target))
            target = index
        if not target.is_file():
            return missing()
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=kind)

    return app


class QuietServer:
    """The viewer's own server, beside the app's. It never takes Ctrl+C over."""

    def __init__(self, app: Any, host: str, port: int) -> None:
        import uvicorn

        class Quiet(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self):  # noqa: ANN202
                yield

            def install_signal_handlers(self) -> None:
                return None

        self.server = Quiet(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        self.task: asyncio.Task | None = None

    async def start(self) -> bool:
        self.task = asyncio.create_task(self.server.serve(), name="viewer")
        await asyncio.sleep(0.3)
        if self.task.done():
            log.warning("the viewer could not start on port %s", self.server.config.port)
            return False
        return True

    async def stop(self) -> None:
        self.server.should_exit = True
        if self.task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.task, 5)


def bind_host(config: Any) -> str:
    return os.environ.get("RIMURU_BIND") or config.host
