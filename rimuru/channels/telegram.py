"""Telegram: talk to Rimuru from your phone.

Plain HTTPS long polling (getUpdates), so no public address is needed and
nothing listens on this computer. A private chat with an allowed person is a
session like any other - listed in the app, with its own folder and clones -
and its replies go back to Telegram. Strangers get one polite "this is a private
assistant" with their ID, and show up in Connections to allow with one click.
Groups are off unless switched on; even then only allowed people are heard.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

import httpx

from .. import commands
from ..ids import new_id
from ..models import Trigger
from .base import ChannelNorms

log = logging.getLogger("rimuru.telegram")

MAX_TEXT = 4000            # Telegram allows 4096 characters per message
MAX_FILE = 10 * 1024 * 1024


class Problem(RuntimeError):
    pass


class TelegramChannel:
    """Private chats: a conversation channel, like the chat page."""

    name = "telegram"
    norms = ChannelNorms(register="chat", word_cap=150,
                         description="One person on Telegram, possibly non-technical. Plain words, short.")

    def __init__(self, *, token: str, settings: Any, system: Any,
                 http: httpx.AsyncClient | None = None, api: str = "https://api.telegram.org") -> None:
        self._token = token
        self.settings = settings
        self.system = system
        self.api = api.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=15.0))
        self.offset = 0
        self.task: asyncio.Task | None = None
        self.state = "starting"
        self.problem = ""
        self.bot = ""

    def channels(self) -> list[Any]:
        return [self, TelegramGroupChannel(self)]

    # ------------------------------------------------------------------ #
    async def call(self, method: str, **params: Any) -> Any:
        try:
            response = await self.http.post(f"{self.api}/bot{self._token}/{method}", json=params)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never the URL: it holds the token.
            raise Problem(f"Telegram could not be reached ({type(exc).__name__}).") from None
        if response.status_code in {401, 404} or data.get("error_code") in {401, 404}:
            raise Problem("Telegram did not accept the bot token.")
        if not data.get("ok"):
            raise Problem(f"Telegram said: {str(data.get('description') or response.status_code)[:160]}")
        return data.get("result")

    async def check(self) -> str:
        me = await self.call("getMe")
        self.bot = "@" + str(me.get("username") or "")
        return self.bot

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._poll(), name="telegram")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        with contextlib.suppress(Exception):
            await self.http.aclose()

    async def _poll(self) -> None:
        wait = 2.0
        while True:
            try:
                if not self.bot:
                    await self.check()
                updates = await self.call("getUpdates", offset=self.offset, timeout=50,
                                          allowed_updates=["message"])
                self.state, self.problem, wait = "connected", "", 2.0
                for update in updates or []:
                    self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
                    try:
                        await self.handle(update)
                    except Exception as exc:  # noqa: BLE001 - one bad message must not stop the bot
                        log.warning("telegram message not handled: %s", type(exc).__name__)
            except asyncio.CancelledError:
                raise
            except Problem as exc:
                self.state, self.problem = "problem", str(exc)
                if "did not accept" in str(exc):
                    return                      # waits for a new token (Connections)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 60.0)
            except Exception as exc:  # noqa: BLE001
                self.state, self.problem = "problem", f"{type(exc).__name__}"
                await asyncio.sleep(wait)
                wait = min(wait * 2, 60.0)

    # ------------------------------------------------------------------ #
    async def handle(self, update: dict[str, Any]) -> None:
        msg = update.get("message") or {}
        chat, user = msg.get("chat") or {}, msg.get("from") or {}
        uid, chat_id = user.get("id"), chat.get("id")
        if uid is None or chat_id is None or user.get("is_bot"):
            return
        name = str(user.get("first_name") or user.get("username") or "someone")[:60]
        text = str(msg.get("text") or msg.get("caption") or "").strip()
        private = chat.get("type") == "private"

        if not self.settings.telegram_allowed(uid):
            if private and self.settings.note_stranger(uid, name, str(user.get("username") or "")):
                await self.send(chat_id, (
                    f"Hi {name}. This is a private assistant, so I can't take requests from you. "
                    f"If this is your Rimuru: your Telegram ID is {uid} - allow it in Rimuru's "
                    f"Connections."))
            return
        if not text:
            if private:
                await self.send(chat_id, "I can only read text messages for now.")
            return
        if private and commands.parse(text) == "init":
            # The same /init as in the app; other commands (/start) are just messages.
            done = await self.system.run_command(f"tg-{chat_id}", text, channel="telegram")
            await self.send(chat_id, done["said"])
        elif private:
            await self.system.human_message(text, channel="telegram", thread_key=f"tg-{chat_id}",
                                            author=name)
        elif self.settings.tg.get("groups"):
            await self.system.ingest(Trigger(
                id=new_id("trigger"), channel="telegram-group", thread_key=f"tg-group-{chat_id}",
                author=name, kind="message", content=text,
                meta={"thread_title": str(chat.get("title") or "Telegram group")[:80]}))

    async def send(self, chat_id: Any, text: str) -> None:
        text = (text or "").replace("**", "").strip() or "..."
        for start in range(0, len(text), MAX_TEXT):
            await self.call("sendMessage", chat_id=chat_id, text=text[start:start + MAX_TEXT],
                            disable_web_page_preview=True)

    async def deliver(self, thread_key: str, text: str) -> str:
        await self.send(thread_key.removeprefix("tg-"), text)
        # The same session is in the app too: keep its chat there complete.
        self.system.store.add_chat("rimuru", text, chat=thread_key)
        return text

    # ------------------------------------------------------------------ #
    async def finish(self, worker: Any, thread: Any, outcome: str, summary: str) -> str:
        """When a job from Telegram is done, send the files it made: on a phone,
        a folder on the computer is out of reach."""
        from ..web.friendly import worker_card

        card = worker_card(self.system.store, worker.session, self.system.config.conversation_channels)
        sent = []
        for rel in (card.get("files") or [])[:3]:
            path = (Path(worker.root) / rel).resolve()
            try:
                path.relative_to(Path(worker.root).resolve())
            except ValueError:
                continue
            if not path.is_file() or path.stat().st_size > MAX_FILE:
                continue
            try:
                response = await self.http.post(
                    f"{self.api}/bot{self._token}/sendDocument",
                    data={"chat_id": thread.key.removeprefix("tg-")},
                    files={"document": (path.name, path.read_bytes())})
                if response.status_code == 200:
                    sent.append(path.name)
            except httpx.HTTPError:
                continue
        return f"sent on Telegram: {', '.join(sent)}" if sent else ""


class TelegramGroupChannel:
    """Group chats (only when switched on): team-chat manners, filtered."""

    name = "telegram-group"
    norms = ChannelNorms()

    def __init__(self, parent: TelegramChannel) -> None:
        self.parent = parent

    async def deliver(self, thread_key: str, text: str) -> str:
        await self.parent.send(thread_key.removeprefix("tg-group-"), text)
        return text
