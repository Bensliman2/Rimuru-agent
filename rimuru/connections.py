"""Connections to the outside: Telegram and GitHub.

Tokens live in .env (catalog.set_key) - never in the settings file, never sent
back to the app. The settings file, in the data folder next to Rimuru's records,
holds who may give Rimuru work and where to look:

  telegram  people allowed to talk to it (by Telegram ID), people who tried
            (to allow with one click), and whether it answers in groups
  github    the repositories to watch, the people whose mentions count, the
            word that calls Rimuru (@rimuru), and how far it has read

Nobody else can make Rimuru do anything: on Telegram it answers strangers only
with "this is a private assistant"; on GitHub it ignores everyone not listed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

TELEGRAM_TOKEN = "TELEGRAM_BOT_TOKEN"
GITHUB_TOKEN = "GITHUB_TOKEN"
TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,}$")
GITHUB_TOKEN_RE = re.compile(r"^(github_pat_[A-Za-z0-9_]{40,}|gh[pousr]_[A-Za-z0-9]{30,})$")
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


class Settings:
    """Who may give Rimuru work, and where it looks - a small JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        try:
            self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("telegram", {}).setdefault("allowed", [])
        self.data["telegram"].setdefault("strangers", [])
        self.data["telegram"].setdefault("groups", False)
        gh = self.data.setdefault("github", {})
        gh.setdefault("repos", [])
        gh.setdefault("allowed", [])
        gh.setdefault("mention", "@rimuru")
        gh.setdefault("checked", {})

    @classmethod
    def for_config(cls, config: Any) -> "Settings":
        return cls(Path(config.db_path).resolve().parent / "connections.json")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---------------------------------------------------------------- telegram
    @property
    def tg(self) -> dict[str, Any]:
        return self.data["telegram"]

    def telegram_allowed(self, user_id: Any) -> bool:
        return any(str(p["id"]) == str(user_id) for p in self.tg["allowed"])

    def allow(self, user_id: Any, name: str = "") -> None:
        if not str(user_id).lstrip("-").isdigit():
            raise ValueError("A Telegram ID is a number.")
        stranger = next((s for s in self.tg["strangers"] if str(s["id"]) == str(user_id)), None)
        if not self.telegram_allowed(user_id):
            self.tg["allowed"].append({"id": int(user_id),
                                       "name": name or (stranger or {}).get("name") or str(user_id)})
        self.tg["strangers"] = [s for s in self.tg["strangers"] if str(s["id"]) != str(user_id)]
        self.save()

    def disallow(self, user_id: Any) -> None:
        self.tg["allowed"] = [p for p in self.tg["allowed"] if str(p["id"]) != str(user_id)]
        self.save()

    def note_stranger(self, user_id: Any, name: str, username: str = "") -> bool:
        """Someone not allowed wrote to the bot. True the first time."""
        if any(str(s["id"]) == str(user_id) for s in self.tg["strangers"]):
            return False
        self.tg["strangers"] = (self.tg["strangers"] + [{
            "id": int(user_id), "name": name, "username": username, "ts": time.time()}])[-20:]
        self.save()
        return True

    # ---------------------------------------------------------------- github
    @property
    def gh(self) -> dict[str, Any]:
        return self.data["github"]

    def set_github(self, *, repos: list[str], allowed: list[str], mention: str | None = None) -> None:
        repos = [r.strip().strip("/") for r in repos if r.strip()]
        bad = [r for r in repos if not REPO_RE.match(r)]
        if bad:
            raise ValueError(f'"{bad[0]}" is not a repository - write it as owner/name.')
        allowed = [a.strip().lstrip("@") for a in allowed if a.strip()]
        bad = [a for a in allowed if not LOGIN_RE.match(a)]
        if bad:
            raise ValueError(f'"{bad[0]}" is not a GitHub username.')
        word = (mention or self.gh["mention"]).strip()
        if not re.fullmatch(r"@?[A-Za-z0-9_-]{2,39}", word):
            raise ValueError("The word that calls Rimuru must be one word, like @rimuru.")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # A newly watched repository is read from now on, not from its whole history.
        self.gh["checked"] = {r: self.gh["checked"].get(r, now) for r in repos}
        self.gh.update(repos=repos, allowed=allowed, mention="@" + word.lstrip("@"))
        self.save()


def token(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def remove_key(env_path: Path, name: str) -> None:
    """Take a token out of .env (and this process)."""
    env_path = Path(env_path)
    if env_path.exists():
        pattern = re.compile(rf"\s*(export\s+)?{re.escape(name)}\s*=")
        lines = [line for line in env_path.read_text(encoding="utf-8").splitlines()
                 if not pattern.match(line)]
        tmp = env_path.with_name(env_path.name + ".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(env_path)
    os.environ.pop(name, None)


class Connections:
    """Starts, stops and reports on the outside connections of one Rimuru."""

    def __init__(self, system: Any) -> None:
        self.system = system
        self.settings = Settings.for_config(system.config)
        self.telegram: Any = None
        self.github: Any = None

    def build(self) -> None:
        """Make the channels whose tokens are set (and stop the others)."""
        from .channels.github_live import GitHubChannel
        from .channels.telegram import TelegramChannel

        system = self.system
        if token(TELEGRAM_TOKEN) and self.telegram is None:
            self.telegram = TelegramChannel(token=token(TELEGRAM_TOKEN), settings=self.settings,
                                            system=system)
            for channel in self.telegram.channels():
                system.channels.register(channel)
            system.pool.finishers["telegram"] = self.telegram.finish
        if token(GITHUB_TOKEN) and self.settings.gh["repos"] and self.github is None:
            self.github = GitHubChannel(token=token(GITHUB_TOKEN), settings=self.settings,
                                        system=system)
            system.channels.register(self.github)
            system.pool.preparers["gh"] = self.github.prepare
            system.pool.finishers["gh"] = self.github.publish

    async def start(self) -> None:
        self.build()
        for channel in (self.telegram, self.github):
            if channel is not None:
                channel.start()

    async def stop(self) -> None:
        for channel in (self.telegram, self.github):
            if channel is not None:
                await channel.stop()

    async def restart(self, which: str) -> None:
        """After a token or the settings changed."""
        old = getattr(self, which)
        if old is not None:
            await old.stop()
            setattr(self, which, None)
            if which == "telegram":
                self.system.pool.finishers.pop("telegram", None)
            else:
                self.system.pool.preparers.pop("gh", None)
                self.system.pool.finishers.pop("gh", None)
        self.build()
        new = getattr(self, which)
        if new is not None:
            new.start()
            await asyncio.sleep(0)

    def status(self) -> dict[str, Any]:
        tg, gh = self.telegram, self.github
        return {
            "telegram": {
                "token_set": bool(token(TELEGRAM_TOKEN)),
                "state": tg.state if tg else ("off" if not token(TELEGRAM_TOKEN) else "starting"),
                "problem": tg.problem if tg else "",
                "bot": tg.bot if tg else "",
                "allowed": self.settings.tg["allowed"],
                "strangers": self.settings.tg["strangers"],
                "groups": self.settings.tg["groups"],
            },
            "github": {
                "token_set": bool(token(GITHUB_TOKEN)),
                "state": gh.state if gh else ("off" if not token(GITHUB_TOKEN) else "waiting for repositories"),
                "problem": gh.problem if gh else "",
                "login": gh.login if gh else "",
                "repos": self.settings.gh["repos"],
                "allowed": self.settings.gh["allowed"],
                "mention": self.settings.gh["mention"],
            },
        }
