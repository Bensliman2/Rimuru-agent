"""A log file, so that what goes wrong quietly can be found afterwards.

Plenty in Rimuru must not stop for a small failure: closing a connection, a
minute's housekeeping, one listener among many. Such a failure used to vanish.
Now it carries on and leaves a line in data/rimuru.log.
"""
from __future__ import annotations

import contextlib
import logging
import os
import logging.handlers
from pathlib import Path
from typing import Iterator

from .safety import scrubbed

log = logging.getLogger("rimuru")


class _NoKeys(logging.Formatter):
    """Messages and tracebacks can quote anything: the log gets the same filter
    as everything else that leaves the app."""

    def format(self, record: logging.LogRecord) -> str:
        return scrubbed(super().format(record))


def setup(folder: Path) -> Path:
    """Log to rimuru.log in the data folder: 1 MB, and the last three before it.
    The console's log goes through the same filter."""
    plain = _NoKeys("%(asctime)s %(levelname)-7s %(name)s  %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(plain)
    path = Path(folder) / "rimuru.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    to_file = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    to_file.setFormatter(plain)
    log.handlers = [h for h in log.handlers if not isinstance(h, logging.handlers.RotatingFileHandler)]
    log.addHandler(to_file)
    level = os.environ.get("RIMURU_LOG_LEVEL", "INFO").upper()
    log.setLevel(getattr(logging, level, logging.INFO))
    return path


@contextlib.contextmanager
def noted(what: str, level: int = logging.WARNING) -> Iterator[None]:
    """For work whose failure must not stop anything: carry on, and say so in the
    log. (asyncio.CancelledError is not an Exception, so stopping still stops.)"""
    try:
        yield
    except Exception:  # noqa: BLE001 - that is the point of it
        log.log(level, "%s failed", what, exc_info=True)
