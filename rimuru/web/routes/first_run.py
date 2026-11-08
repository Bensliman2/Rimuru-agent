"""The first start: from nothing to a model that works, in about two minutes.

A new person sees three choices - a free model, a key they have, or Ollama on
this computer - instead of a chat that only pretends to work. Whatever they pick
is tried with one small request first, and saved only if it answered: a choice
that fails leaves nothing behind. The demo is still there, if they ask for it.
"""
from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException

from ...llm import catalog
from ..shared import Web


@contextlib.contextmanager
def _trying_key(name: str, value: str) -> Iterator[None]:
    """The key lives in this process only for the try. It reaches .env once it works."""
    before = os.environ.get(name) if name else None
    if name:
        os.environ[name] = value
    try:
        yield
    finally:
        if name and before is None:
            os.environ.pop(name, None)
        elif name:
            os.environ[name] = before


def add_routes(app: FastAPI, web: Web) -> None:
    system = web.system

    @app.get("/api/setup")
    async def setup_options() -> dict[str, Any]:
        """The choices on the first-start screen."""
        try:
            _url, ollama = await catalog.list_models("ollama", timeout=1.5)
        except (ValueError, RuntimeError):
            ollama = []                  # not running: the choice is left out
        known = catalog.describe(system.config.setup, demo_forced=False)
        return {"presets": known["presets"], "ollama": ollama,
                "providers": [{k: p[k] for k in ("id", "label", "hint")} for p in known["providers"]
                              if p["needs_key"] and p["id"] != "custom"]}

    @app.post("/api/setup/connect")
    async def connect(payload: dict[str, Any]) -> dict[str, Any]:
        """Try a choice, and keep it if it answered."""
        setup = catalog.load(system.config.models_path)
        key = str(payload.get("key") or "").strip()
        env, model, offered = "", "", []
        try:
            if payload.get("preset"):
                setup = await catalog.apply_preset(setup, str(payload["preset"]))
            else:
                provider = catalog.PROVIDERS.get(str(payload.get("provider") or ""))
                if provider is None:
                    raise ValueError("Pick a service first.")
                env = provider.key_env
                _url, offered = await catalog.list_models(provider.id, key=key)
                model = str(payload.get("model") or "") or catalog.suggest_model(provider.id, offered)
                if not model:
                    raise ValueError(f"{provider.label} lists no models for this key.")
                setup.models[provider.id] = catalog.ModelEntry(name=provider.id, provider=provider.id, model=model)
                setup.roles = {"router": provider.id, "worker": provider.id}
            with _trying_key(env, key):
                checks = await catalog.try_setup(setup)
            if not all(check["ok"] for check in checks):
                return {"ok": False, "checks": checks, "offered": offered[:300], "model": model}
            if env:
                catalog.set_key(system.config.env_path, env, key)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        catalog.save(setup)
        await system.apply_models()
        return {"ok": True, "checks": checks}

    @app.post("/api/setup/skip")
    async def skip() -> dict[str, Any]:
        """They would rather look around first: the demo, and no setup screen."""
        system.store.set_setting("setup_skipped", "1")
        return {"ok": True}
