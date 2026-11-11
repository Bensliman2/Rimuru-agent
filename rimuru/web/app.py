"""FastAPI surface for the dashboard.

Everything the UI shows is read out of the Store, and every state change the
engine makes is mirrored onto a WebSocket, so the dashboard is a live view of
the running system rather than a periodic poll of a summary.
"""
from __future__ import annotations

import asyncio
import mimetypes
import time
from contextlib import asynccontextmanager
from pathlib import Path

from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ..bus import STORY
from ..config import CONFIG
from ..logs import log, noted
from ..system import RimuruSystem
from .. import __version__
from . import routes
from .auth import COOKIE, MAX_AGE, Auth, login_page
from .shared import Guard, Web
from .viewer import QuietServer, bind_host, create_viewer

STATIC = Path(__file__).parent / "static"
# Windows can say .js is text/plain, and browsers refuse to run such a module.
mimetypes.add_type("text/javascript", ".js")


def create_app(*, speed: float = 4.0, autostart: str | None = None) -> FastAPI:
    system = RimuruSystem(CONFIG, speed=speed)

    web = Web(system)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await system.start()
        viewer = None
        # What clones build opens on a port of its own (rimuru/web/viewer.py). Only
        # a served app has one (run.py sets view_port); tests do not.
        if app.state.view_port:
            viewer = QuietServer(create_viewer(web, auth), bind_host(CONFIG), app.state.view_port)
            if not await viewer.start():
                app.state.view_port = 0
        if autostart:
            await asyncio.sleep(0.5)
            system.start_scenario(autostart)
        yield
        if viewer is not None:
            with noted("stopping the viewer"):
                await viewer.stop()
        with noted("stopping"):
            await system.stop()

    app = FastAPI(title="Rimuru Super-agent", docs_url="/api/docs", lifespan=lifespan)
    app.state.system = system
    app.state.view_port = 0
    _foreign = Guard(CONFIG).refuse
    auth = Auth(CONFIG)
    if auth.enabled:
        log.info("a password is set: every page and call asks for it")
    elif Guard(CONFIG).open_host:
        log.warning("Rimuru answers on a network address without a password: set RIMURU_PASSWORD")

    @app.middleware("http")
    async def signed_in(request, call_next):
        # Only with RIMURU_PASSWORD set (rimuru/web/auth.py).
        if auth.enabled and not auth.open_path(request.url.path) and not auth.valid(request.cookies.get(COOKIE)):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"error": "Sign in first."}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.middleware("http")
    async def local_only(request, call_next):
        refused = _foreign(request.headers, request.method,
                           request.client.host if request.client else None)
        if refused:
            return JSONResponse({"error": refused}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def revalidate_assets(request, call_next):
        # Without this, browsers heuristically cache the dashboard and keep
        # showing an old UI after an update. no-cache still allows caching - it
        # just revalidates against the ETag, which costs one 304.
        response = await call_next(request)
        if request.url.path in {"/", "/inside"} or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # ------------------------------------------------------------------ #
    # pages
    # ------------------------------------------------------------------ #
    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    def page(name: str) -> HTMLResponse:
        """A page, with the icons both pages share put in."""
        return HTMLResponse((STATIC / name).read_text(encoding="utf-8").replace(
            "<!-- symbols -->", (STATIC / "symbols.svg").read_text(encoding="utf-8")))

    # ------------------------------------------------------------------ #
    # signing in (only with RIMURU_PASSWORD), health, a problem report
    # ------------------------------------------------------------------ #
    @app.get("/login")
    async def login_form() -> Response:
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(login_page())

    @app.post("/login")
    async def login(request: Request) -> Response:
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        given = (parse_qs((await request.body()).decode("utf-8", "replace")).get("password") or [""])[0]
        verdict = auth.check(given, request.client.host if request.client else "")
        if verdict is None:
            return HTMLResponse(login_page("Too many wrong tries. Wait ten minutes."), status_code=429)
        if not verdict:
            return HTMLResponse(login_page("That is not the password."), status_code=401)
        response = RedirectResponse("/", status_code=303)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(COOKIE, auth.token(), max_age=MAX_AGE, httponly=True, samesite="strict",
                            secure=secure)
        return response

    @app.get("/logout")
    async def logout() -> Response:
        response = RedirectResponse("/login" if auth.enabled else "/", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/api/health")
    async def health() -> dict:
        """For monitors and Docker: is it up, and can it save."""
        return {"ok": not system.store.save_problem, "version": __version__,
                "saving": system.store.save_problem or "ok",
                "uptime": round(time.time() - system.started_ts, 1) if system.started_ts else 0}

    @app.get("/api/report")
    async def problem_report() -> Response:
        """A file to attach to a bug report: the log (keys were taken out as it was
        written), the version and the state of things. No chats, no files."""
        import io
        import json
        import platform
        import zipfile

        from ..safety import scrubbed

        buffer = io.BytesIO()
        folder = system.config.db_path.parent
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for log_file in sorted(folder.glob("rimuru.log*")):
                archive.writestr(log_file.name, scrubbed(log_file.read_text(encoding="utf-8", errors="replace")))
            overview = system.overview()
            about = {"version": __version__, "python": platform.python_version(),
                     "system": platform.platform(), "demo": overview.get("demo"),
                     "models": overview.get("models_note"), "shell": overview.get("shell"),
                     "store": overview.get("store"), "blocked": overview.get("blocked"),
                     "saving": system.store.save_problem or "ok"}
            archive.writestr("about.json", scrubbed(json.dumps(about, indent=2, default=str)))
        return Response(buffer.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="rimuru-report-{time.strftime("%Y%m%d-%H%M")}.zip"'})

    @app.get("/")
    async def index() -> HTMLResponse:
        """The chat app, for people who do not use agents."""
        return page("index.html")

    @app.get("/inside")
    async def inside() -> HTMLResponse:
        """The technical dashboard: every session, tool call and decision."""
        return page("inside.html")

    # ------------------------------------------------------------------ #
    # the API, one file per feature (rimuru/web/routes/)
    # ------------------------------------------------------------------ #
    for feature in routes.ALL:
        feature.add_routes(app, web)

    # ------------------------------------------------------------------ #
    # live feed
    # ------------------------------------------------------------------ #
    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        # Browsers let any page open a WebSocket, so check who is asking.
        if _foreign(socket.headers, "WS", socket.client.host if socket.client else None) or \
                not auth.valid(socket.cookies.get(COOKIE)):
            await socket.close(code=1008)
            return
        await socket.accept()
        queue = system.bus.subscribe_queue()
        try:
            # Prime the client with recent history so a late join is not blank.
            await socket.send_json({"topic": "_backlog", "events": system.bus.recent(400, topics=set(STORY))})
            while True:
                event = await queue.get()
                await socket.send_json(event)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - one page's connection, nothing more
            log.debug("a page's live feed ended", exc_info=True)
        finally:
            system.bus.unsubscribe_queue(queue)

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
