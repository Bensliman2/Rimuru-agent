"""Offline tests for choosing models (config/models.toml) and for the
OpenAI-compatible client that Kimi, OpenAI, DeepSeek and Ollama run on.

No network: HTTP is faked.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from rimuru.config import Config
from rimuru.llm import MockClient, build_client, catalog, kimi
from rimuru.llm.base import Msg, ProviderAccountError, ToolSpec
from rimuru.llm.openai_client import OpenAICompatClient


def _config(tmp_path, text: str | None = None) -> Config:
    config = Config()
    config.provider, config.demo = "", False
    config.models_path = tmp_path / "models.toml"
    if text is not None:
        config.models_path.write_text(text, encoding="utf-8")
    config.setup = catalog.load(config.models_path)
    return config


# --------------------------------------------------------------------------- #
# choosing models: nothing fixed in code, everything explained
# --------------------------------------------------------------------------- #
def test_no_models_yet_means_demo_mode_with_a_plain_reason(tmp_path):
    config = _config(tmp_path)
    assert config.is_demo()
    assert any("No models are set up yet" in p for p in config.setup.check())
    assert isinstance(build_client(config, "router"), MockClient)
    assert config.model_for("worker") == "demo"


def test_each_job_gets_the_model_chosen_for_it(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = _config(tmp_path, """
[roles]
router = "fast"
worker = "strong"

[models.fast]
provider = "openai"
model = "gpt-test-mini"

[models.strong]
provider = "anthropic"
model = "claude-test"
max_tokens = 9000
""")
    assert not config.is_demo(), config.setup.check()
    router = build_client(config, "router")
    worker = build_client(config, "worker")
    assert isinstance(router, OpenAICompatClient)
    assert (router.model, router.base_url) == ("gpt-test-mini", "https://api.openai.com/v1")
    assert worker.model == "claude-test" and worker.max_tokens_override == 9000
    # The watchdog and the filter use Rimuru's model unless told otherwise.
    assert build_client(config, "watchdog").model == "gpt-test-mini"
    assert config.model_for("filter") == "gpt-test-mini"
    assert config.provider_label_for("worker") == "Claude (Anthropic)"


def test_a_missing_key_is_named_not_guessed(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = _config(tmp_path, """
[roles]
router = "ds"
worker = "ds"

[models.ds]
provider = "deepseek"
model = "deepseek-chat"
""")
    problems = config.setup.check()
    assert config.is_demo()
    assert any("no DeepSeek key" in p and "DEEPSEEK_API_KEY" in p for p in problems)


def test_the_models_file_round_trips_and_explains_itself(tmp_path):
    body = {"roles": {"router": "kimi-k3.1", "worker": "local"},
            "models": [{"name": "kimi-k3.1", "provider": "kimi", "model": "kimi-k3.1-pro",
                        "max_tokens": 8000},
                       {"name": "local", "provider": "ollama", "model": "qwen2.5:7b",
                        "reasoning": False}]}
    setup = catalog.from_request(tmp_path / "models.toml", body)
    catalog.save(setup)
    text = setup.path.read_text(encoding="utf-8")
    assert text.startswith("# Rimuru's models")
    assert '[models."kimi-k3.1"]' in text           # names with dots are quoted
    again = catalog.load(setup.path)
    assert again.roles == {"router": "kimi-k3.1", "worker": "local"}
    assert again.models["kimi-k3.1"].max_tokens == 8000
    assert again.models["local"].reasoning is False
    assert again.models["local"].needs_key() is False


@pytest.mark.parametrize("body, says", [
    ({"models": [{"name": "a", "provider": "nope", "model": "x"}]}, "unknown provider"),
    ({"models": [{"name": "a", "provider": "openai", "model": "x"}], "roles": {"router": "b"}},
     "not in the list"),
    ({"models": [{"name": "a", "provider": "custom", "model": "x", "api_key_env": "lower"}]},
     "not a valid .env variable"),
    ({"models": [{"name": "a", "provider": "custom", "model": "x", "base_url": "ftp://x"}]},
     "http:// or https://"),
    ({"models": [{"name": "bad name!", "provider": "openai", "model": "x"}]}, "not a usable name"),
])
def test_bad_settings_are_refused_with_a_reason(tmp_path, body, says):
    with pytest.raises(ValueError, match=says):
        catalog.from_request(tmp_path / "models.toml", body)


def test_a_key_goes_to_env_and_is_never_shown(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "placeholder")          # restored afterwards
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nKIMI_API_KEY=old\n", encoding="utf-8")
    catalog.set_key(env, "KIMI_API_KEY", "sk-new-1234567890")
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines == ["OTHER=1", "KIMI_API_KEY=sk-new-1234567890"]

    setup = catalog.ModelSetup(path=tmp_path / "models.toml")
    setup.models["k"] = catalog.ModelEntry(name="k", provider="kimi", model="kimi-x")
    setup.roles = {"router": "k", "worker": "k"}
    shown = json.dumps(catalog.describe(setup, demo_forced=False))
    assert "sk-new-1234567890" not in shown
    assert catalog.describe(setup, demo_forced=False)["models"][0]["key_present"] is True

    with pytest.raises(ValueError):
        catalog.set_key(env, "KIMI_API_KEY", "two words")
    with pytest.raises(ValueError):
        catalog.set_key(env, "lowercase", "sk-x")


def test_kimi_finds_its_region_from_the_key(monkeypatch):
    asked = []

    def fake_get(url, headers=None, timeout=None):
        asked.append(url)
        if "moonshot.ai" in url:
            return httpx.Response(401, text="invalid key")
        return httpx.Response(200, json={"data": [{"id": "kimi-b"}, {"id": "kimi-a"}]})

    monkeypatch.setattr(kimi.httpx, "get", fake_get)
    base, ids = kimi.discover("sk-cn-key")
    assert base == "https://api.moonshot.cn/v1" and ids == ["kimi-a", "kimi-b"]
    assert len(asked) == 2

    monkeypatch.setenv("KIMI_API_KEY", "sk-cn-key")
    entry = catalog.ModelEntry(name="k", provider="kimi", model="kimi-a")
    assert catalog.base_url_for(entry) == "https://api.moonshot.cn/v1"


def test_the_settings_api_only_answers_this_computer(tmp_path, monkeypatch):
    """The app stores keys and can point models anywhere, so other websites and
    other hostnames must be refused."""
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    for name, value in {"db_path": tmp_path / "t.db", "workspace": tmp_path / "ws",
                        "models_path": tmp_path / "models.toml", "env_path": tmp_path / ".env",
                        "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    body = {"roles": {"router": "m", "worker": "m"},
            "models": [{"name": "m", "provider": "openai", "model": "gpt-test"}],
            "keys": {"OPENAI_API_KEY": "sk-from-the-panel-123"}}

    with TestClient(create_app(speed=50)) as client:
        page = client.get("/inside")
        assert page.status_code == 200
        assert all(f'data-view="{v}"' in page.text for v in ("rimuru", "clones", "watchdog"))
        assert client.get("/api/models", headers={"host": "evil.example"}).status_code == 403
        assert client.post("/api/models", json=body,
                           headers={"origin": "http://evil.example"}).status_code == 403
        assert not (tmp_path / "models.toml").exists()

        sneaky = {**body, "keys": {"RIMURU_SHELL": "host"}}
        assert client.post("/api/models", json=sneaky).status_code == 400

        saved = client.post("/api/models", json=body)
        assert saved.status_code == 200, saved.text
        assert "sk-from-the-panel-123" not in saved.text
        assert (tmp_path / "models.toml").exists()
        assert "OPENAI_API_KEY=sk-from-the-panel-123" in (tmp_path / ".env").read_text()


# --------------------------------------------------------------------------- #
# the OpenAI-compatible client
# --------------------------------------------------------------------------- #
def _client(handler, **kwargs) -> OpenAICompatClient:
    client = OpenAICompatClient("https://example.invalid/v1", "sk-test", "kimi-test", **kwargs)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def test_retries_429_then_parses_tool_calls_usage_and_reasoning():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0.01"}, text="slow down")
        body = json.loads(request.content)
        assert body["tool_choice"] == "auto"
        assert body["tools"][0]["function"]["name"] == "dispatch_task"
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "Dispatching.",
                    "reasoning_content": "It is a defect report.",
                    "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "dispatch_task",
                                     "arguments": json.dumps({"title": "fix exporter"})},
                    }],
                },
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 9, "cached_tokens": 100},
        })

    async def run():
        client = _client(handler)
        tools = [ToolSpec("dispatch_task", "d", {"type": "object", "properties": {}})]
        resp = await client.complete(system="s", messages=[Msg("user", "hi")], tools=tools)
        await client.aclose()
        return resp

    resp = asyncio.run(run())
    assert calls["n"] == 2, "the 429 should have been retried"
    assert resp.tool_calls[0].name == "dispatch_task"
    assert resp.tool_calls[0].args == {"title": "fix exporter"}
    assert resp.thinking == "It is a defect report."
    assert resp.usage.cache_read_tokens == 100      # Moonshot's top-level field


def test_a_400_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, text="bad tool schema")

    async def run():
        client = _client(handler)
        with pytest.raises(RuntimeError, match="bad tool schema"):
            await client.complete(system="s", messages=[Msg("user", "hi")], tools=[])
        await client.aclose()

    asyncio.run(run())
    assert calls["n"] == 1


def _history() -> list[Msg]:
    return [
        Msg("user", "Your task is tsk_1"),
        Msg("assistant", [
            {"type": "reasoning", "text": "read the file first"},
            {"type": "text", "text": "Reading."},
            {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.py"}},
        ]),
        Msg("user", [
            {"type": "tool_result", "tool_use_id": "c1", "content": "print(1)"},
            {"type": "text", "text": "New event on a thread you subscribe to"},
        ]),
    ]


def test_translation_echoes_reasoning_only_when_asked():
    plain = OpenAICompatClient("u", "k", "m", echo_reasoning=False)._messages("sys", _history())
    echo = OpenAICompatClient("u", "k", "m", echo_reasoning=True)._messages("sys", _history())

    assistant_plain = next(m for m in plain if m["role"] == "assistant")
    assistant_echo = next(m for m in echo if m["role"] == "assistant")
    assert "reasoning_content" not in assistant_plain      # e.g. DeepSeek rejects it
    assert assistant_echo["reasoning_content"] == "read the file first"   # Kimi wants it
    assert assistant_echo["tool_calls"][0]["function"]["name"] == "read_file"

    roles = [m["role"] for m in echo]
    # tool result must directly follow the assistant turn that called it
    assert roles == ["system", "user", "assistant", "tool", "user"]



def test_out_of_credit_is_recognised_at_once_not_retried():
    """Found in real use: Kimi answered every call with HTTP 429 'suspended due to
    insufficient balance', and each call was retried six times for nothing."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": {
            "message": "Your account org-x <ak-y> is suspended due to insufficient balance, "
                       "please recharge your account or check your plan and billing details",
            "type": "exceeded_current_quota_error"}})

    client = _client(handler)
    client.provider_label = "Kimi (Moonshot)"
    with pytest.raises(ProviderAccountError) as err:
        asyncio.run(client.complete(system="s", messages=[Msg("user", "hi")], tools=[]))
    assert err.value.reason == "credit" and calls["n"] == 1
    assert err.value.plain() == "Kimi (Moonshot) says the account is out of credit"


def test_a_refused_key_is_recognised():
    client = _client(lambda request: httpx.Response(401, json={"error": {"message": "Invalid Authentication"}}))
    with pytest.raises(ProviderAccountError) as err:
        asyncio.run(client.complete(system="s", messages=[Msg("user", "hi")], tools=[]))
    assert err.value.reason == "key"
