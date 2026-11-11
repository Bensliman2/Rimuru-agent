"""A backup model, for when the chosen one fails.

Free models stumble, paid accounts run out of credit, services go down. With a
backup chosen in Models ("Backup model"), a call that fails on the chosen model -
after its own retries - is made again on the backup, and the backup takes the
calls for a few minutes before the chosen model is tried again. Nobody has to
notice, except in the log.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger("rimuru.llm")


class FallbackClient:
    """Looks like one client; is the chosen model and its backup."""

    name = "fallback"
    # Set on the pair, so whichever answers behaves the same.
    _SHARED = frozenset({"stream", "on_retry", "max_tokens_override"})

    def __init__(self, primary: Any, backup: Any, *, rest_seconds: float = 300.0) -> None:
        object.__setattr__(self, "primary", primary)
        object.__setattr__(self, "backup", backup)
        object.__setattr__(self, "rest_seconds", rest_seconds)
        object.__setattr__(self, "_down_until", 0.0)
        object.__setattr__(self, "switches", 0)

    def _current(self) -> Any:
        return self.backup if time.monotonic() < self._down_until else self.primary

    def __getattr__(self, name: str) -> Any:
        # provider_label, last_progress, model...: from the client in use now.
        return getattr(self._current(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._SHARED:
            for client in (self.primary, self.backup):
                setattr(client, name, value)
        else:
            object.__setattr__(self, name, value)

    async def complete(self, **kwargs: Any):
        if time.monotonic() >= self._down_until:
            try:
                return await self.primary.complete(**kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure: the backup answers instead
                object.__setattr__(self, "_down_until", time.monotonic() + self.rest_seconds)
                object.__setattr__(self, "switches", self.switches + 1)
                log.warning("%s failed (%s); the backup model takes over for %.0f minutes",
                            getattr(self.primary, "model", "the chosen model"),
                            type(exc).__name__, self.rest_seconds / 60)
        return await self.backup.complete(**kwargs)

    async def aclose(self) -> None:
        for client in (self.primary, self.backup):
            closing = getattr(client, "aclose", None)
            if closing is not None:
                try:
                    await closing()
                except Exception:  # noqa: BLE001
                    log.debug("closing a client failed", exc_info=True)
