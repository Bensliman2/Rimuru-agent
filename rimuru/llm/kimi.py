"""Kimi (Moonshot AI): finding the endpoint a key belongs to.

Moonshot serves an OpenAI-compatible API, so Kimi runs on the generic
OpenAICompatClient. Keys are region-locked: a key from platform.moonshot.ai works
only against api.moonshot.ai, one from platform.moonshot.cn only against
api.moonshot.cn. Rather than make the person know which they have, both are
tried and the one that accepts the key is kept. Which *model* to use is never
guessed here - the person chooses it (config/models.toml, or the Models button).
"""
from __future__ import annotations

import httpx

ENDPOINTS = ("https://api.moonshot.ai/v1", "https://api.moonshot.cn/v1")


def discover(api_key: str, base_url: str | None = None, timeout: float = 15.0) -> tuple[str, list[str]]:
    """Return (base_url, model_ids) for the endpoint that accepts this key."""
    if not api_key:
        raise RuntimeError("no Kimi key - add KIMI_API_KEY to .env or use the Models button")
    candidates = [base_url.rstrip("/")] if base_url else list(ENDPOINTS)
    errors: list[str] = []
    for url in candidates:
        try:
            response = httpx.get(
                f"{url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            errors.append(f"{url}: {type(exc).__name__}")
            continue
        if response.status_code == 200:
            ids = [m.get("id", "") for m in response.json().get("data", []) if m.get("id")]
            return url, sorted(ids)
        errors.append(f"{url}: HTTP {response.status_code}")
    raise RuntimeError("Kimi did not accept the key (" + "; ".join(errors) + ")")
