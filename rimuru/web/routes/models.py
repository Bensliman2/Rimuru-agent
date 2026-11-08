"""Models: which AI does which job (config/models.toml)."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ...config import ROOT
from ...llm import catalog
from ..shared import Web


def add_routes(app: FastAPI, web: Web) -> None:
    system = web.system

    def models_state(note: str = "") -> dict[str, Any]:
        state = catalog.describe(system.config.setup, demo_forced=system.config.demo_forced(),
                                 root=ROOT)
        state["note"] = note
        state["summary"] = system.provider_note
        return state

    @app.get("/api/models")
    async def get_models() -> dict[str, Any]:
        return models_state()

    @app.post("/api/models")
    async def save_models(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            setup = catalog.from_request(system.config.models_path, payload)
            # A key may only be stored under a name some model actually reads.
            usable = {e.key_env() for e in setup.models.values() if e.key_env()}
            for env_name, value in (payload.get("keys") or {}).items():
                if not str(value or "").strip():
                    continue
                if env_name not in usable or env_name.startswith("RIMURU_"):
                    raise ValueError(f"{env_name} is not the key of any model in the list.")
                catalog.set_key(system.config.env_path, env_name, str(value))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        catalog.save(setup)
        await system.apply_models()
        if system.config.demo_forced():
            note = "Saved. Rimuru was started with --demo, so it uses these after a restart."
        elif system.config.is_demo():
            note = "Saved, but Rimuru is still in demo mode: " + system.config.setup.check()[0]
        else:
            router = system.config.setup.entry_for("router")
            worker = system.config.setup.entry_for("worker")
            note = (f"Saved. Rimuru now thinks with {router.model}; new clones use "
                    f"{worker.model}. Clones already working finish on their model.")
        return models_state(note)

    @app.post("/api/models/preset")
    async def use_preset(payload: dict[str, Any]) -> dict[str, Any]:
        """A ready-made setup, in one click."""
        try:
            setup = await catalog.apply_preset(system.config.setup, str(payload.get("id") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        catalog.save(setup)
        await system.apply_models()
        return models_state(f"Saved. Rimuru now thinks with {setup.entry_for('router').model}; new clones use "
                            f"{setup.entry_for('worker').model}.")

    @app.post("/api/models/find")
    async def find_models(payload: dict[str, Any]) -> dict[str, Any]:
        """Which models can this key use? Fills the model list in the panel."""
        provider = str(payload.get("provider") or "")
        preset = catalog.PROVIDERS.get(provider)
        if preset is None:
            raise HTTPException(400, "Choose a provider first.")
        base_url = str(payload.get("base_url") or "").strip().rstrip("/")
        key = str(payload.get("key") or "").strip()
        if not key and preset.key_env:
            # A key already stored goes only where it is meant to: the provider's
            # own address, or one already saved for that provider.
            saved = {e.base_url.rstrip("/") for e in system.config.setup.models.values()
                     if e.provider == provider and e.base_url}
            if base_url and base_url != preset.base_url.rstrip("/") and base_url not in saved:
                raise HTTPException(400, "Paste the key for this address.")
            env = str(payload.get("api_key_env") or "").strip()
            probe = catalog.ModelEntry(name="probe", provider=provider, model="",
                                       api_key_env=env if catalog.ENV_RE.fullmatch(env) else "")
            key = probe.key()
        try:
            found_url, ids = await catalog.list_models(provider, key=key, base_url=base_url)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from None
        return {"base_url": found_url, "models": ids}
