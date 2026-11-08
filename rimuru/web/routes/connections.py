"""Connections: Telegram and GitHub (rimuru/connections.py)."""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException

from ... import connections as conn
from ...channels.github_live import GitHubChannel, Problem as GitHubProblem
from ...channels.telegram import Problem as TelegramProblem, TelegramChannel
from ...llm import catalog
from ..shared import Web


def add_routes(app: FastAPI, web: Web) -> None:
    system = web.system

    @app.get("/api/connections")
    async def connections_status() -> dict[str, Any]:
        """What is connected and who may give Rimuru work. Tokens are never sent."""
        return system.connections.status()

    @app.post("/api/connections/telegram")
    async def telegram_setup(payload: dict[str, Any]) -> dict[str, Any]:
        conns = system.connections
        token = str(payload.get("token") or "").strip()
        if token:
            if not conn.TELEGRAM_TOKEN_RE.match(token):
                raise HTTPException(400, "That does not look like a bot token. @BotFather gives one "
                                         "like 123456789:AAH... - a number, a colon and a long code.")
            probe = TelegramChannel(token=token, settings=conns.settings, system=system)
            try:
                await probe.check()
            except TelegramProblem as exc:
                raise HTTPException(400, str(exc)) from None
            finally:
                await probe.http.aclose()
            catalog.set_key(system.config.env_path, conn.TELEGRAM_TOKEN, token)
        if "groups" in payload:
            conns.settings.tg["groups"] = bool(payload.get("groups"))
            conns.settings.save()
        if token:
            await conns.restart("telegram")
        return conns.status()

    @app.post("/api/connections/telegram/people")
    async def telegram_people(payload: dict[str, Any]) -> dict[str, Any]:
        settings = system.connections.settings
        try:
            if payload.get("allow", True):
                settings.allow(payload.get("id"), str(payload.get("name") or "")[:60])
            else:
                settings.disallow(payload.get("id"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return system.connections.status()

    @app.post("/api/connections/github")
    async def github_setup(payload: dict[str, Any]) -> dict[str, Any]:
        conns = system.connections
        token = str(payload.get("token") or "").strip()
        listed = lambda v: v if isinstance(v, list) else re.split(r"[\s,]+", str(v or ""))  # noqa: E731
        repos = [str(r) for r in listed(payload.get("repos", conns.settings.gh["repos"])) if str(r).strip()]
        if token and not conn.GITHUB_TOKEN_RE.match(token):
            raise HTTPException(400, "That does not look like a GitHub token (github_pat_... or ghp_...).")
        use = token or conn.token(conn.GITHUB_TOKEN)
        if not use:
            raise HTTPException(400, "Paste a GitHub token first.")
        try:
            conns.settings.set_github(
                repos=repos, mention=payload.get("mention"),
                allowed=[str(a) for a in listed(payload.get("allowed", conns.settings.gh["allowed"]))])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        probe = GitHubChannel(token=use, settings=conns.settings, system=system)
        try:
            login = await probe.check()
            for repo in repos:
                await probe.call("GET", f"/repos/{repo}")
        except GitHubProblem as exc:
            raise HTTPException(400, f"{exc} Check the token can see {', '.join(repos) or 'the repositories'}.") from None
        finally:
            await probe.http.aclose()
        if not conns.settings.gh["allowed"]:
            conns.settings.set_github(repos=repos, allowed=[login])     # you, by default
        if token:
            catalog.set_key(system.config.env_path, conn.GITHUB_TOKEN, token)
        await conns.restart("github")
        return conns.status()

    @app.delete("/api/connections/{which}")
    async def disconnect(which: str) -> dict[str, Any]:
        if which not in {"telegram", "github"}:
            raise HTTPException(404, "No such connection.")
        conn.remove_key(system.config.env_path,
                        conn.TELEGRAM_TOKEN if which == "telegram" else conn.GITHUB_TOKEN)
        await system.connections.restart(which)
        return system.connections.status()
