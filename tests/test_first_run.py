"""The first start: a real choice instead of fake jobs, and nothing saved until it works."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from rimuru.llm import catalog
from rimuru.llm.base import ProviderAccountError
from rimuru.web.app import create_app

KEY = "sk-test-first-run-0123456789"


@pytest.fixture
def app(tmp_path, monkeypatch):
    from rimuru import config as config_module

    conf = config_module.Config()
    conf.db_path = tmp_path / "data" / "t.db"
    conf.workspace = tmp_path / "ws"
    conf.sessions_dir = tmp_path / "data" / "sessions"
    conf.models_path = tmp_path / "config" / "models.toml"
    conf.env_path = tmp_path / ".env"
    conf.demo = False
    conf.provider = ""
    (tmp_path / "config").mkdir()
    conf.setup = catalog.load(conf.models_path)
    monkeypatch.setattr("rimuru.web.app.CONFIG", conf)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    async def offered(provider, *, key="", base_url="", timeout=15.0):
        if provider == "ollama":
            raise ValueError("not running")
        if key != KEY and provider != "kilo":
            raise ValueError("Groq (free tier) did not accept that key.")
        return "http://x", ["cohere/north-mini-code:free", "stealth/glyph-cluster", "llama-3.3-70b-versatile", "other"]

    monkeypatch.setattr(catalog, "list_models", offered)
    with TestClient(create_app(speed=50)) as client:
        yield SimpleNamespace(client=client, conf=conf, path=tmp_path)


def works(monkeypatch, bad_models=()):
    seen: list[str] = []

    async def fake(entry, *, seconds=60.0):
        seen.append(entry.model)
        ok = entry.model not in bad_models
        return {"ok": ok, "model": entry.model, "label": entry.label, "seconds": 1.2,
                "problem": "" if ok else "It cannot use tools."}

    monkeypatch.setattr(catalog, "try_model", fake)
    return seen


def work(app) -> dict:
    return app.client.get("/api/work", params={"chat": "direct"}).json()


def test_a_new_install_opens_on_the_setup_and_a_forced_demo_does_not(app, tmp_path):
    assert work(app)["setup_needed"] is True
    options = app.client.get("/api/setup").json()
    assert options["presets"][0]["id"] == "free" and options["ollama"] == []
    assert any(p["id"] == "groq" for p in options["providers"])
    assert not any(p["id"] in {"kilo", "ollama", "custom"} for p in options["providers"]), "those need no key"

    app.conf.demo = True
    assert work(app)["setup_needed"] is False, "started with --demo: the person asked for that"


def test_a_key_that_works_is_saved_and_rimuru_is_really_on(app, monkeypatch):
    tried = works(monkeypatch)
    done = app.client.post("/api/setup/connect", json={"provider": "groq", "key": KEY}).json()
    assert done["ok"] and tried == ["llama-3.3-70b-versatile"], "a sensible model is chosen for them"
    assert done["checks"][0]["who"] == "Rimuru and the clones"
    assert KEY not in str(done), "a key never comes back"
    assert f"GROQ_API_KEY={KEY}" in (app.path / ".env").read_text(encoding="utf-8")
    assert 'model = "llama-3.3-70b-versatile"' in app.conf.models_path.read_text(encoding="utf-8")
    state = work(app)
    assert state["setup_needed"] is False and state["demo"] is False


def test_a_model_that_fails_leaves_nothing_behind_and_another_can_be_tried(app, monkeypatch):
    works(monkeypatch, bad_models={"llama-3.3-70b-versatile"})
    failed = app.client.post("/api/setup/connect", json={"provider": "groq", "key": KEY}).json()
    assert failed["ok"] is False and failed["checks"][0]["problem"] == "It cannot use tools."
    assert "other" in failed["offered"]
    assert not (app.path / ".env").exists() and not app.conf.models_path.exists()
    assert "GROQ_API_KEY" not in os.environ, "the key was only there for the try"
    assert work(app)["setup_needed"] is True, "still the first start"

    done = app.client.post("/api/setup/connect", json={"provider": "groq", "key": KEY, "model": "other"}).json()
    assert done["ok"] and 'model = "other"' in app.conf.models_path.read_text(encoding="utf-8")


def test_a_key_the_provider_refuses_is_said_in_plain_words(app, monkeypatch):
    works(monkeypatch)
    answer = app.client.post("/api/setup/connect", json={"provider": "groq", "key": "nope"})
    assert answer.status_code == 400 and "did not accept" in answer.text
    assert not (app.path / ".env").exists()


def test_the_free_choice_tries_both_models(app, monkeypatch):
    tried = works(monkeypatch)
    done = app.client.post("/api/setup/connect", json={"preset": "free"}).json()
    assert done["ok"] and sorted(tried) == ["cohere/north-mini-code:free", "stealth/glyph-cluster"]
    assert {c["who"] for c in done["checks"]} == {"Rimuru", "The clones"}
    assert work(app)["demo"] is False


def test_looking_around_first_is_remembered(app):
    assert app.client.post("/api/setup/skip").json()["ok"]
    state = work(app)
    assert state["setup_needed"] is False and state["demo"] is True


class _Client:
    max_attempts = 6

    def __init__(self, answer):
        self.answer = answer

    async def complete(self, **_kw):
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer == "slow":
            await asyncio.sleep(5)
        return self.answer


def tried(monkeypatch, answer, seconds=60.0):
    monkeypatch.setattr(catalog, "make_client", lambda entry, concurrency=4: _Client(answer))
    entry = catalog.ModelEntry(name="m", provider="groq", model="x")
    return asyncio.run(catalog.try_model(entry, seconds=seconds))


def test_trying_a_model_says_what_went_wrong(monkeypatch):
    assert tried(monkeypatch, SimpleNamespace(tool_calls=[object()]))["ok"]
    assert "cannot use tools" in tried(monkeypatch, SimpleNamespace(tool_calls=[]))["problem"]
    assert "within a minute" in tried(monkeypatch, "slow", seconds=0.01)["problem"]
    assert "did not accept" in tried(monkeypatch, ProviderAccountError("key", "Groq"))["problem"]
    leaked = tried(monkeypatch, RuntimeError(f"bad request with {KEY}"))["problem"]
    assert KEY not in leaked, "an error can quote the key"


def test_a_sensible_first_model(monkeypatch):
    assert catalog.suggest_model("anthropic", ["claude-opus-4", "claude-sonnet-4", "claude-sonnet-5-5"]) == "claude-sonnet-5-5"
    assert catalog.suggest_model("groq", ["whisper", "llama-3.3-70b-versatile"]) == "llama-3.3-70b-versatile"
    assert catalog.suggest_model("custom", ["a", "b"]) == "a"
    assert catalog.suggest_model("groq", []) == ""
