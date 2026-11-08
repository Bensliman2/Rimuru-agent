"""Channels and their interaction norms (article s4.2).

People communicate differently in a group chat, in email and in a pull request
review, so the agent has to vary both the density and the register of what it
says. Detailed reasoning suits a terminal; posted unchanged into a group chat it
just buries everyone.

The two-level architecture already does most of this: a Worker's reasoning and
tool traffic never leave its session, and only the Router speaks. What is left
is per-channel manners. The chat word cap is the one rule the article calls out
as verified, so it is enforced here in code rather than left to the prompt.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..safety import scrubbed

CHAT_WORD_CAP = 100
_FENCED = re.compile(r"```.*?(?:```|$)", re.S)
CODE_LEFT_OUT = "[…]"                # the elision mark: it reads the same in every language


@dataclass
class ChannelNorms:
    register: str = "chat"           # chat | async
    word_cap: int | None = CHAT_WORD_CAP
    description: str = "Keep it short. Interrupt only for a decision or a sign-off."
    code_on_request: bool = False    # code blocks only in a reply that says it was asked for


ASYNC_NORMS = ChannelNorms(
    register="async",
    word_cap=None,
    description=(
        "Structured and information-dense: the background, what changed, how it "
        "was verified."
    ),
)


class Channel(Protocol):
    name: str
    norms: ChannelNorms

    async def deliver(self, thread_key: str, text: str) -> str:
        ...


def enforce_norms(text: str, norms: ChannelNorms) -> tuple[str, str]:
    """Apply the channel's hard rules. Returns (text_to_send, note_for_the_model)."""
    text = (text or "").strip()
    if norms.word_cap is None:
        return text, ""
    words = text.split()
    if len(words) <= norms.word_cap:
        return text, ""
    clipped = " ".join(words[: norms.word_cap]).rstrip(",;:") + " ..."
    return clipped, (
        f"Your reply ran to {len(words)} words and this channel is capped at "
        f"{norms.word_cap}. It was truncated. Say less next time: a conclusion "
        f"and, if you need one, a direct question."
    )


@dataclass
class OutboundMessage:
    ts: float
    channel: str
    thread_key: str
    text: str
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts, "channel": self.channel, "thread_key": self.thread_key,
            "text": self.text, "truncated": self.truncated,
        }


@dataclass
class ChannelRegistry:
    bus: Any = None
    channels: dict[str, Channel] = field(default_factory=dict)
    outbox: list[OutboundMessage] = field(default_factory=list)

    def register(self, channel: Channel) -> None:
        self.channels[channel.name] = channel

    def norms_for(self, name: str) -> ChannelNorms:
        channel = self.channels.get(name)
        return channel.norms if channel else ChannelNorms()

    async def send(self, channel_name: str, thread_key: str, text: str, *,
                   include_code: bool = False, **who: Any) -> tuple[str, str]:
        """Deliver a reply. Returns (what_was_sent, note_for_the_model). `who`: a
        clone speaking for itself, shown as such in the chat."""
        norms = self.norms_for(channel_name)
        text = scrubbed(text)                               # no reply carries a key
        # Whether the person asked for code is for Rimuru to judge, in whatever
        # language they asked: it says so with include_code.
        left_out = norms.code_on_request and not include_code and "```" in text
        if left_out:
            text = _FENCED.sub(CODE_LEFT_OUT, text)
        sent, note = enforce_norms(text, norms)

        message = OutboundMessage(
            ts=time.time(), channel=channel_name, thread_key=thread_key,
            text=sent, truncated=bool(note),
        )
        if left_out:
            note = (note + " " if note else "") + (
                f"The code was left out and {CODE_LEFT_OUT} sent in its place: on this channel code goes "
                f"only to someone who asked for it. If they did, reply again with include_code.")
        self.outbox.append(message)
        if len(self.outbox) > 2000:
            del self.outbox[:500]

        channel = self.channels.get(channel_name)
        if channel is not None:
            try:
                await channel.deliver(thread_key, sent, **who)
            except Exception as exc:
                note = (note + " " if note else "") + f"delivery failed: {exc}"

        if self.bus:
            self.bus.emit("channel.sent", **message.to_dict())
        return sent, note

    def recent(self, limit: int = 60) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.outbox[-limit:]]


class ConsoleChannel:
    """The chat page: one person talking to Rimuru directly."""

    name = "console"
    norms = ChannelNorms(
        register="chat", word_cap=150,
        description="One person, possibly non-technical. Plain words, short.",
    )

    def __init__(self, bus: Any, store: Any = None) -> None:
        self.bus = bus
        self.store = store

    async def deliver(self, thread_key: str, text: str, **who: Any) -> str:
        if self.store is not None:
            self.store.add_chat("rimuru", text, chat=thread_key, **who)
        self.bus.emit("console.message", thread_key=thread_key, text=text)
        return text
