"""LLM backends, and the factory that gives each job its model."""
from __future__ import annotations

from ..config import Config
from .base import LLMClient, LLMResponse, Msg, ToolCall, ToolSpec
from .mock import MockClient

__all__ = [
    "LLMClient", "LLMResponse", "Msg", "ToolCall", "ToolSpec", "MockClient", "build_client",
]


def build_client(config: Config, kind: str, *, speed: float = 1.0) -> LLMClient:
    """The client for one job - router | worker | watchdog | filter - as chosen in
    config/models.toml. The article's split (s2.1/s2.2) is the person's to make:
    typically a quick model routes and the strongest one does the work.

    In demo mode (forced, or nothing usable set up yet) every job gets the offline
    stand-in model, and the app says so plainly.
    """
    if config.is_demo():
        return MockClient(speed=speed)
    from .catalog import make_client

    entry = config.setup.entry_for(kind)
    client = make_client(entry, concurrency=config.llm_concurrency)
    chosen = getattr(config.setup, "roles", None) or {}
    backup = config.setup.entry_for("fallback") if kind in {"router", "worker"} and chosen.get("fallback") else None
    if backup is not None and backup.name != entry.name and not backup.problems():
        from .fallback import FallbackClient

        client = FallbackClient(client, make_client(backup, concurrency=config.llm_concurrency))
    # Only Rimuru itself is cut in on (a person's message stops a turn about
    # something else), so only its calls stream: closing the stream stops the
    # provider writing an answer that will be thrown away.
    client.stream = kind == "router"
    return client
