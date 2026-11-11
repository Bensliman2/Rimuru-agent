"""Configuration, loaded from the environment with offline-safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The project folder: config/models.toml and .env live here.
ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    # The keys the Models and Connections panels save: in .env here - or where
    # RIMURU_ENV_FILE says (in Docker: the data folder, so they survive a rebuild).
    load_dotenv(os.environ.get("RIMURU_ENV_FILE") or None)
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # --- models ---
    # Which model does which job is the person's choice, kept in
    # config/models.toml - the Models button in the app edits it. No model is
    # fixed in code. API keys stay in .env.
    models_path: Path = field(default_factory=lambda: Path(
        os.environ.get("RIMURU_MODELS") or ROOT / "config" / "models.toml").resolve())
    env_path: Path = field(default_factory=lambda: Path(
        os.environ.get("RIMURU_ENV_FILE") or ROOT / ".env").resolve())
    # Demo mode: an offline stand-in model, no API calls. Forced by --demo (or
    # the older RIMURU_PROVIDER=mock); otherwise used until models are set up.
    demo: bool = field(default_factory=lambda: _bool("RIMURU_DEMO", False))
    provider: str = field(default_factory=lambda: (os.environ.get("RIMURU_PROVIDER") or "").lower())
    # The loaded setup (rimuru.llm.catalog.ModelSetup), filled in by the system.
    setup: Any = None

    # Concurrent requests to a remote provider, across all sessions. Rate limits
    # are per account, so this is one shared gate rather than one per session.
    llm_concurrency: int = field(default_factory=lambda: _int("RIMURU_LLM_CONCURRENCY", 4))

    # --- engine ---
    max_workers: int = field(default_factory=lambda: _int("RIMURU_MAX_WORKERS", 6))
    router_max_turn_batch: int = field(default_factory=lambda: _int("RIMURU_ROUTER_MAX_BATCH", 12))
    router_idle_sleep: float = 0.05

    # Context budgeting (Inline & Spill), in characters.
    inline_content_cap: int = field(default_factory=lambda: _int("RIMURU_INLINE_CAP", 280))
    router_context_budget: int = field(default_factory=lambda: _int("RIMURU_ROUTER_BUDGET", 60_000))
    worker_context_budget: int = field(default_factory=lambda: _int("RIMURU_WORKER_BUDGET", 120_000))

    # --- watchdog ---
    watchdog_interval: float = field(default_factory=lambda: float(_int("RIMURU_WATCHDOG_INTERVAL", 30)))
    worker_stall_seconds: float = field(default_factory=lambda: float(_int("RIMURU_STALL_SECONDS", 120)))
    worker_max_steps: int = field(default_factory=lambda: _int("RIMURU_WORKER_MAX_STEPS", 40))
    worker_max_tokens: int = field(default_factory=lambda: _int("RIMURU_WORKER_MAX_TOKENS", 16384))
    # When the provider refuses the account (out of credit, key refused), try
    # again after this many seconds, doubling up to two minutes.
    blocked_retry_seconds: float = 30.0

    # --- tools ---
    enable_bash: bool = field(default_factory=lambda: _bool("RIMURU_ENABLE_BASH", False))
    # auto | docker | simulated | host. auto = the Docker sandbox when it is available.
    shell: str = field(default_factory=lambda: (os.environ.get("RIMURU_SHELL") or "auto").lower())
    # Where work that does not come from a chat session happens: practice runs
    # and team channels. Chat sessions each get their own folder (sessions_dir).
    workspace: Path = field(
        default_factory=lambda: Path(os.environ.get("RIMURU_WORKSPACE", "./data/practice")).resolve()
    )
    # Each chat session's own folder lives here (rimuru/sessions.py) - outside the
    # shared workspace, so resetting the workspace never wipes a session's work.
    sessions_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("RIMURU_SESSIONS", "./data/sessions")).resolve()
    )
    # Caps: how long one job may take, and how many AI requests an hour the app
    # may make in all (0: no limit). The person sets them; the app says when one stops work.
    job_minutes: int = field(default_factory=lambda: _int("RIMURU_JOB_MINUTES", 45))
    max_requests_per_hour: int = field(default_factory=lambda: _int("RIMURU_MAX_REQUESTS_PER_HOUR", 0))
    # A new job within this long of the last one finishing in the same session
    # goes to that same clone, which remembers the work (worker/pool.py).
    warm_clone_seconds: float = field(default_factory=lambda: float(os.environ.get("RIMURU_WARM_CLONE_SECONDS", "600")))
    # The person's skills: one Markdown file each, for every clone (rimuru/skills.py).
    skills_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("RIMURU_SKILLS") or ROOT / "skills").resolve())

    # Channels that are one continuous conversation with a person. The Router
    # owns these; workers never subscribe to them.
    # Telegram private chats are the same kind: the person, on their phone.
    conversation_channels: frozenset = frozenset({"console", "telegram"})

    # --- web ---
    host: str = field(default_factory=lambda: os.environ.get("RIMURU_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("RIMURU_PORT", 8800))

    # --- storage ---
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("RIMURU_DB", "./data/rimuru.db")).resolve())

    def demo_forced(self) -> bool:
        return self.demo or self.provider in {"mock", "demo"}

    def is_demo(self) -> bool:
        """True when no real model is in use: forced, or nothing usable set up."""
        return self.demo_forced() or self.setup is None or not self.setup.ready

    def model_for(self, kind: str) -> str:
        """The model ID doing a job: router | worker | watchdog | filter."""
        if self.is_demo():
            return "demo"
        entry = self.setup.entry_for(kind)
        return entry.model if entry else "not set"

    def provider_label_for(self, kind: str) -> str:
        if self.is_demo():
            return "Demo (offline)"
        entry = self.setup.entry_for(kind)
        return entry.label if entry else ""

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
