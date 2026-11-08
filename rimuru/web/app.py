"""FastAPI surface for the dashboard.

Everything the UI shows is read out of the Store, and every state change the
engine makes is mirrored onto a WebSocket, so the dashboard is a live view of
the running system rather than a periodic poll of a summary.
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..bus import STORY
from ..config import CONFIG
from ..logs import log, noted
from ..system import RimuruSystem
from . import routes
from .shared import Web

STATIC = Path(__file__).parent / "static"
# Windows can say .js is text/plain, and browsers refuse to run such a module.
mimetypes.add_type("text/javascript", ".js")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


def _host_of(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.split(":", 1)[0]


def create_app(*, speed: float = 4.0, autostart: str | None = None) -> FastAPI:
    system = RimuruSystem(CONFIG, speed=speed)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await system.start()
        if autostart:
            await asyncio.sleep(0.5)
            system.start_scenario(autostart)
        yield
        with noted("stopping"):
            await system.stop()

    app = FastAPI(title="Rimuru Super-agent", docs_url="/api/docs", lifespan=lifespan)
    app.state.system = system

    # The app stores API keys and can point models at any server, so it answers
    # only this computer: no other website may drive it (cross-site requests),
    # and no other hostname may reach it (DNS rebinding). Serving on a network
    # address (RIMURU_HOST=0.0.0.0) is a deliberate choice; RIMURU_ALLOWED_HOSTS
    # lists extra names to accept.
    allowed_hosts = LOCAL_HOSTS | {CONFIG.host.lower()} | {
        h.strip().lower() for h in os.environ.get("RIMURU_ALLOWED_HOSTS", "").split(",") if h.strip()}
    open_host = CONFIG.host in {"0.0.0.0", "::"}
    # Unless the person serves the app beyond this computer on purpose, only this
    # computer gets an answer - by the caller's address, not only the Host header.
    # (Other machines reach Rimuru only through the remote hands' own port.)
    beyond = open_host or bool(os.environ.get("RIMURU_ALLOWED_HOSTS", "").strip())
    here = {"127.0.0.1", "::1", "localhost", "testclient"}

    def _foreign(headers, method: str, caller: str | None = None) -> str | None:
        host = headers.get("host", "")
        if not beyond and caller and caller not in here and not caller.startswith("127."):
            return "This app only answers on this computer."
        if not open_host and _host_of(host) not in allowed_hosts:
            return "This app only answers on this computer."
        origin = headers.get("origin")
        if origin and method not in {"GET", "HEAD", "OPTIONS"} and \
                urlparse(origin).netloc.lower() != host.lower():
            return "Requests from other websites are refused."
        return None

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
    web = Web(system)
    for feature in routes.ALL:
        feature.add_routes(app, web)

    # ------------------------------------------------------------------ #
    # live feed
    # ------------------------------------------------------------------ #
    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        # Browsers let any page open a WebSocket, so check who is asking.
        if _foreign(socket.headers, "WS", socket.client.host if socket.client else None):
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
