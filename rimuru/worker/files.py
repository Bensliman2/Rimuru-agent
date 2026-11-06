"""A clone's files: list, read, write - here, or in a folder on another machine.

A clone only ever names files relative to its folder. LocalFiles keeps every
path inside that folder; the remote hands (rimuru/hands/) use the very same
class on their own machine, so the rule is identical everywhere.
"""
from __future__ import annotations

from pathlib import Path

SKIP = {"__pycache__", ".pytest_cache", ".git", "node_modules"}
MAX_READ = 2_000_000


class Refused(Exception):
    """Asked for something outside the folder."""


class LocalFiles:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def path(self, rel: str) -> Path:
        candidate = (self.root / (rel or ".")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise Refused("that path is outside the workspace") from None
        return candidate

    def name(self, rel: str) -> str:
        return self.path(rel).relative_to(self.root).as_posix()

    async def list(self, rel: str = ".") -> list[str] | None:
        """Every file under rel, relative to the folder; None if not a folder."""
        top = self.path(rel)
        if not top.is_dir():
            return None
        return sorted(
            p.relative_to(self.root).as_posix() for p in top.rglob("*")
            if p.is_file() and not SKIP.intersection(p.relative_to(self.root).parts))

    async def read(self, rel: str) -> str | None:
        """The file's text; None when there is no such file."""
        path = self.path(rel)
        if not path.is_file():
            return None
        # Text mode, like write(): line endings round-trip (CRLF on Windows).
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(MAX_READ)

    async def write(self, rel: str, text: str) -> None:
        path = self.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
