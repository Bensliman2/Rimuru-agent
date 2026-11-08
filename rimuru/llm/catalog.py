"""Which model does which job - chosen by the person, never fixed in code.

The choice lives in config/models.toml, which the Models button in the app
reads and writes, and which can also be edited by hand:

    [roles]
    router = "kimi-fast"      # Rimuru: talks with you and hands out the work
    worker = "kimi-strong"    # the clones that do the work
    # watchdog, filter        # optional: they use Rimuru's model unless set

    [models.kimi-fast]
    provider = "kimi"
    model = "kimi-k3.1-pro-highspeed"

API keys never go in that file. Each provider reads its key from .env, so the
models file can be shared or committed without leaking anything.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..logs import noted
from ..safety import scrubbed
from .base import Msg, ProviderAccountError, ToolSpec

ROLES = ("router", "worker", "watchdog", "filter")
REQUIRED = ("router", "worker")

ROLE_INFO = {
    "router": ("Rimuru", "Talks with you and hands out the work. A fast model feels best."),
    "worker": ("Clones", "Do the actual work: read, write and test code. "
                         "Pick your most capable model."),
    "watchdog": ("Watchdog", "Checks that clones are making progress. "
                             "A fast, cheap model is fine."),
    "filter": ("Noise filter", "Decides whether a group-chat message needs Rimuru. "
                               "A fast, cheap model is fine."),
}

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}")
ENV_RE = re.compile(r"[A-Z][A-Z0-9_]{1,63}")


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str                     # openai (any OpenAI-compatible API) | anthropic
    base_url: str = ""            # "" = the person gives it (kimi: found from the key)
    key_env: str = ""             # where its key lives in .env; "" = no key needed
    key_aliases: tuple[str, ...] = ()
    reasoning: bool = False       # send reasoning back on later turns of a tool loop
    hint: str = ""
    hourly: int = 0               # a free plan's requests per hour, when known


PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("kimi", "Kimi (Moonshot)", "openai", "", "KIMI_API_KEY", ("MOONSHOT_API_KEY",),
             reasoning=True,
             hint="A key from platform.moonshot.ai or platform.moonshot.cn - "
                  "the region is found from the key."),
    Provider("anthropic", "Claude (Anthropic)", "anthropic", "https://api.anthropic.com",
             "ANTHROPIC_API_KEY", hint="A key from console.anthropic.com."),
    Provider("openai", "OpenAI", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY",
             hint="A key from platform.openai.com."),
    Provider("deepseek", "DeepSeek", "openai", "https://api.deepseek.com", "DEEPSEEK_API_KEY",
             hint="A key from platform.deepseek.com."),
    Provider("ollama", "Ollama (on this computer)", "openai", "http://localhost:11434/v1",
             hint="Runs models on your own machine. No key needed; Ollama must be running."),
    # Free tiers (github.com/mnfst/awesome-free-llm-apis). Free plans are rate
    # limited, and several may keep or log what you send: nothing confidential.
    Provider("kilo", "Kilo Code (free, no key)", "openai", "https://api.kilo.ai/api/gateway",
             hint="Free models with no account and no key, about 200 requests an hour. Pick a "
                  "model ending in :free. Prompts may be logged - send nothing confidential.",
             hourly=200),
    Provider("groq", "Groq (free tier)", "openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
             hint="Very fast. A free key from console.groq.com/keys - no credit card; "
                  "about 30 requests a minute and 1,000 a day."),
    Provider("gemini", "Google Gemini (free tier)", "openai",
             "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
             hint="A free key from aistudio.google.com/app/apikey - no credit card. Free-tier "
                  "prompts may be used by Google to improve its products."),
    Provider("mistral", "Mistral (free mode)", "openai", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
             hint="A free key from console.mistral.ai/api-keys - no credit card. Free-mode "
                  "prompts may train Mistral's models unless you opt out."),
    Provider("openrouter", "OpenRouter (free models)", "openai", "https://openrouter.ai/api/v1",
             "OPENROUTER_API_KEY",
             hint="A free key from openrouter.ai/keys; models ending in :free cost nothing, "
                  "about 50 requests a day."),
    Provider("nvidia", "NVIDIA NIM (free)", "openai", "https://integrate.api.nvidia.com/v1",
             "NVIDIA_API_KEY",
             hint="A free key with the NVIDIA Developer Program (build.nvidia.com); about 40 "
                  "requests a minute. Use is logged - nothing confidential."),
    Provider("zai", "Z AI - GLM (free models)", "openai", "https://api.z.ai/api/paas/v4", "ZAI_API_KEY",
             hint="A free key from z.ai; GLM-4.7-Flash is free, one request at a time."),
    Provider("cohere", "Cohere (free trial)", "openai", "https://api.cohere.ai/compatibility/v1",
             "COHERE_API_KEY",
             hint="A free trial key from dashboard.cohere.com - 1,000 calls a month, "
                  "non-commercial use only."),
    Provider("custom", "Other OpenAI-compatible server", "openai", "", "CUSTOM_API_KEY",
             hint="vLLM, LM Studio, OpenRouter and others: give the server's address."),
)}


# --------------------------------------------------------------------------- #
# the setup
# --------------------------------------------------------------------------- #
@dataclass
class ModelEntry:
    name: str
    provider: str
    model: str
    base_url: str = ""
    api_key_env: str = ""
    max_tokens: int | None = None
    reasoning: bool | None = None

    @property
    def preset(self) -> Provider | None:
        return PROVIDERS.get(self.provider)

    @property
    def label(self) -> str:
        return self.preset.label if self.preset else self.provider

    def key_env(self) -> str:
        return self.api_key_env or (self.preset.key_env if self.preset else "")

    def needs_key(self) -> bool:
        return bool(self.key_env())

    def key(self) -> str:
        names = [self.key_env(), *(self.preset.key_aliases if self.preset and not self.api_key_env else ())]
        return next((os.environ[n].strip() for n in names if n and os.environ.get(n, "").strip()), "")

    def echo_reasoning(self) -> bool:
        if self.reasoning is not None:
            return self.reasoning
        return bool(self.preset and self.preset.reasoning)

    def problems(self) -> list[str]:
        out = []
        if self.preset is None:
            out.append(f'"{self.name}": unknown provider "{self.provider}" '
                       f'(use one of: {", ".join(PROVIDERS)}).')
            return out
        if not self.model:
            out.append(f'"{self.name}": no model ID.')
        if self.provider == "custom" and not self.base_url:
            out.append(f'"{self.name}": an OpenAI-compatible server needs its address (base_url).')
        if self.needs_key() and not self.key():
            out.append(f'"{self.name}": no {self.label} key. Add it with the Models button, '
                       f'or put {self.key_env()}=... in .env.')
        return out

    def to_public(self) -> dict[str, Any]:
        """What the app may show. Never the key itself."""
        return {
            "name": self.name, "provider": self.provider, "provider_label": self.label,
            "model": self.model, "base_url": self.base_url, "api_key_env": self.api_key_env,
            "key_env": self.key_env(), "needs_key": self.needs_key(),
            "key_present": bool(self.key()) if self.needs_key() else True,
            "max_tokens": self.max_tokens, "reasoning": self.reasoning,
        }


@dataclass
class ModelSetup:
    path: Path
    roles: dict[str, str] = field(default_factory=dict)
    models: dict[str, ModelEntry] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)     # reading / connecting problems

    def name_for(self, role: str) -> str:
        name = self.roles.get(role, "")
        if not name and role in {"watchdog", "filter"}:
            name = self.roles.get("router", "")
        return name

    def entry_for(self, role: str) -> ModelEntry | None:
        return self.models.get(self.name_for(role))

    def check(self) -> list[str]:
        """Everything that stands between this setup and real use, in plain words."""
        out = list(self.problems)
        if not self.path.exists() and not self.models:
            out.append("No models are set up yet. Click Models to choose them.")
            return out
        for role in ROLES:
            name = self.roles.get(role, "")
            who = ROLE_INFO[role][0]
            if not name:
                if role in REQUIRED:
                    out.append(f"Choose a model for {who}.")
                continue
            if name not in self.models:
                out.append(f'{who} is set to "{name}", which is not in the list of models.')
        seen = set()
        for role in ROLES:
            entry = self.entry_for(role)
            if entry and entry.name not in seen:
                seen.add(entry.name)
                out += entry.problems()
        return out

    @property
    def ready(self) -> bool:
        return not self.check()


def load(path: Path) -> ModelSetup:
    setup = ModelSetup(path=Path(path))
    if not setup.path.exists():
        return setup
    try:
        data = tomllib.loads(setup.path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        setup.problems.append(f"{setup.path.name} could not be read: {exc}")
        return setup
    roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
    setup.roles = {r: str(roles[r]).strip() for r in ROLES if str(roles.get(r) or "").strip()}
    for name, raw in (data.get("models") or {}).items():
        if not isinstance(raw, dict):
            setup.problems.append(f'"{name}" in {setup.path.name} is not a [models.{name}] table.')
            continue
        try:
            max_tokens = int(raw["max_tokens"]) if raw.get("max_tokens") else None
        except (TypeError, ValueError):
            setup.problems.append(f'"{name}": max_tokens must be a whole number.')
            max_tokens = None
        setup.models[str(name)] = ModelEntry(
            name=str(name),
            provider=str(raw.get("provider", "")).strip().lower(),
            model=str(raw.get("model", "")).strip(),
            base_url=str(raw.get("base_url", "")).strip(),
            api_key_env=str(raw.get("api_key_env", "")).strip(),
            max_tokens=max_tokens,
            reasoning=raw["reasoning"] if isinstance(raw.get("reasoning"), bool) else None,
        )
    return setup


HEADER = """\
# Rimuru's models: which AI does which job.
# Change them with the Models button in the app, or edit this file and restart.
#
# [roles] picks a model, by name, for each job:
#   router    Rimuru itself - talks with you and hands out the work. Fast feels best.
#   worker    the clones that do the work. Use your most capable model.
#   watchdog  checks that clones make progress. Optional: uses the router's model.
#   filter    judges group-chat messages. Optional: uses the router's model.
#
# Each [models.<name>] is one model you can use:
#   provider     kimi | anthropic | openai | deepseek | ollama | custom, or a free tier:
#                kilo (no key) | groq | gemini | mistral | openrouter | nvidia | zai | cohere
#   model        the model's ID at that provider
#   base_url     optional; needed for custom (kimi finds its region from the key)
#   api_key_env  optional; the .env variable holding the key (default per provider:
#                KIMI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY)
#   max_tokens   optional; the longest answer it may write in one go
#   reasoning    optional; send its reasoning back on later turns (on for kimi)
#
# API keys never go in this file - they live in .env, which is never shared.
"""


def _string(value: str) -> str:
    return json.dumps(value)          # a JSON string is a valid TOML basic string


def _table_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _string(name)


def dumps(setup: ModelSetup) -> str:
    lines = [HEADER, "[roles]"]
    lines += [f"{role} = {_string(setup.roles[role])}" for role in ROLES if setup.roles.get(role)]
    for entry in setup.models.values():
        lines += ["", f"[models.{_table_key(entry.name)}]",
                  f"provider = {_string(entry.provider)}", f"model = {_string(entry.model)}"]
        if entry.base_url:
            lines.append(f"base_url = {_string(entry.base_url)}")
        if entry.api_key_env:
            lines.append(f"api_key_env = {_string(entry.api_key_env)}")
        if entry.max_tokens:
            lines.append(f"max_tokens = {int(entry.max_tokens)}")
        if entry.reasoning is not None:
            lines.append(f"reasoning = {'true' if entry.reasoning else 'false'}")
    return "\n".join(lines) + "\n"


def save(setup: ModelSetup) -> None:
    setup.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = setup.path.with_name(setup.path.name + ".tmp")
    tmp.write_text(dumps(setup), encoding="utf-8")
    tmp.replace(setup.path)


# Ready-made setups: a quick model for Rimuru (it routes and talks, so its speed
# is what you feel) and a stronger one for the clones (they do the work).
PRESETS: dict[str, dict[str, str]] = {
    "free": {"label": "Free and fast", "provider": "kilo",
             "about": "No account, no key. A quick model for Rimuru and a stronger one for the clones. "
                      "Free services may log what you send, so nothing confidential.",
             "router": "cohere/north-mini-code:free", "worker": "stealth/glyph-cluster"},
}


async def apply_preset(setup: ModelSetup, preset_id: str) -> ModelSetup:
    """Use a preset: its models are added to the ones you have, and take the jobs.
    Free models come and go, so it first checks they are still offered."""
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise ValueError("There is no such preset.")
    try:
        _url, offered = await list_models(preset["provider"])
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"Could not reach {PROVIDERS[preset['provider']].label}: {exc}") from None
    gone = [preset[role] for role in ("router", "worker") if preset[role] not in offered]
    if gone:
        raise ValueError(f"{', '.join(gone)} is no longer offered. Pick another with Add a model.")
    setup.models["preset-rimuru"] = ModelEntry(name="preset-rimuru", provider=preset["provider"], model=preset["router"])
    setup.models["preset-clones"] = ModelEntry(name="preset-clones", provider=preset["provider"], model=preset["worker"])
    setup.roles = {"router": "preset-rimuru", "worker": "preset-clones"}
    return setup


def from_request(path: Path, body: dict[str, Any]) -> ModelSetup:
    """Build a setup from what the Models panel sends. Raises ValueError with a
    message fit to show the person."""
    setup = ModelSetup(path=Path(path))
    for raw in body.get("models") or []:
        name = str(raw.get("name", "")).strip()
        if not NAME_RE.fullmatch(name):
            raise ValueError(f'"{name}" is not a usable name: letters, digits, dot, dash or '
                             "underscore, up to 48 characters.")
        if name in setup.models:
            raise ValueError(f'Two models are called "{name}".')
        provider = str(raw.get("provider", "")).strip().lower()
        if provider not in PROVIDERS:
            raise ValueError(f'"{name}": unknown provider "{provider}".')
        env = str(raw.get("api_key_env") or "").strip()
        if env and not ENV_RE.fullmatch(env):
            raise ValueError(f'"{env}" is not a valid .env variable name (capitals, digits, _).')
        base_url = str(raw.get("base_url") or "").strip()
        if base_url and not re.match(r"https?://", base_url):
            raise ValueError(f'"{name}": the address must start with http:// or https://.')
        max_tokens = raw.get("max_tokens")
        try:
            max_tokens = int(max_tokens) if max_tokens not in (None, "", 0) else None
        except (TypeError, ValueError):
            raise ValueError(f'"{name}": the answer length must be a whole number.') from None
        if max_tokens is not None and not 256 <= max_tokens <= 1_000_000:
            raise ValueError(f'"{name}": the answer length must be between 256 and 1,000,000 tokens.')
        reasoning = raw.get("reasoning")
        setup.models[name] = ModelEntry(
            name=name, provider=provider, model=str(raw.get("model", "")).strip(),
            base_url=base_url, api_key_env=env, max_tokens=max_tokens,
            reasoning=reasoning if isinstance(reasoning, bool) else None,
        )
    roles = body.get("roles") or {}
    for role in ROLES:
        name = str(roles.get(role) or "").strip()
        if name:
            if name not in setup.models:
                raise ValueError(f'{ROLE_INFO[role][0]} is set to "{name}", which is not in the list.')
            setup.roles[role] = name
    return setup


# --------------------------------------------------------------------------- #
# keys
# --------------------------------------------------------------------------- #
def set_key(env_path: Path, name: str, value: str) -> None:
    """Store an API key in .env (and in this process). The key is never sent
    back to the app, logged, or written anywhere else."""
    if not ENV_RE.fullmatch(name):
        raise ValueError(f'"{name}" is not a valid .env variable name.')
    value = value.strip()
    # Real keys never contain spaces, quotes, backslashes or '#'. Refuse rather
    # than quote or alter: a silently changed key fails later, confusingly.
    if not value or re.search(r"[\s'\"\\#\0]", value):
        raise ValueError("That key looks empty or broken - paste it again.")
    value_out = value
    env_path = Path(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out, written = [], False
    pattern = re.compile(rf"\s*(export\s+)?{re.escape(name)}\s*=")
    for line in lines:
        if pattern.match(line):
            if not written:
                out.append(f"{name}={value_out}")
                written = True
            continue
        out.append(line)
    if not written:
        out.append(f"{name}={value_out}")
    tmp = env_path.with_name(env_path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(env_path)
    os.environ[name] = value


def all_keys(setup: ModelSetup) -> list[str]:
    """Every key in use - for leak checks, never for display."""
    return sorted({k for e in setup.models.values() if (k := e.key())})


# --------------------------------------------------------------------------- #
# connecting
# --------------------------------------------------------------------------- #
_KIMI_BASE: dict[str, str] = {}


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def base_url_for(entry: ModelEntry) -> str:
    """Where to send requests. Kimi keys are region-locked, so a Kimi model
    without an address is sent wherever the key is accepted (asked once)."""
    if entry.base_url:
        return entry.base_url.rstrip("/")
    if entry.provider == "kimi":
        key = entry.key()
        cached = _KIMI_BASE.get(_fingerprint(key))
        if cached:
            return cached
        from . import kimi

        url, _ = kimi.discover(key)
        _KIMI_BASE[_fingerprint(key)] = url
        return url
    return (entry.preset.base_url if entry.preset else "").rstrip("/")


def make_client(entry: ModelEntry, *, concurrency: int = 4):
    if entry.preset is None:
        raise ValueError(f'unknown provider "{entry.provider}"')
    if entry.preset.kind == "anthropic":
        from .anthropic_client import AnthropicClient

        client = AnthropicClient(entry.key(), entry.model,
                                 base_url=entry.base_url.rstrip("/") or None)
    else:
        from .openai_client import OpenAICompatClient

        client = OpenAICompatClient(base_url_for(entry), entry.key(), entry.model,
                                    concurrency=concurrency,
                                    echo_reasoning=entry.echo_reasoning())
    client.max_tokens_override = entry.max_tokens
    client.provider_label = entry.label
    return client


# The model a new key starts with: the first of these the provider offers. A guess -
# it is tried before anything is saved, and the person can pick another.
_SUGGEST: dict[str, tuple[str, ...]] = {
    "kimi": ("kimi-k2", "kimi-latest", "moonshot"),
    "anthropic": ("sonnet", "haiku", "opus"),
    "openai": ("-mini", "gpt-5", "gpt-4"),
    "deepseek": ("deepseek-chat", "deepseek"),
    "groq": ("llama-3.3-70b", "llama", "qwen"),
    "gemini": ("flash", "gemini"),
    "mistral": ("mistral-small", "mistral-medium", "mistral-large"),
    "openrouter": (":free",),
    "nvidia": ("llama-3.3", "llama", "nemotron"),
    "zai": ("flash", "glm"),
    "cohere": ("command-r", "command"),
}


def suggest_model(provider: str, offered: list[str]) -> str:
    for wanted in _SUGGEST.get(provider, ()):
        found = [model for model in offered if wanted in model.lower()]
        if found:
            return max(found)            # the newest-looking of them
    return offered[0] if offered else ""


_HELLO = ToolSpec(name="reply", description="Send a reply to the person.",
                  schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})


def _what_it_said(exc: Exception) -> str:
    """What the provider said, without the address and the JSON around it."""
    text = scrubbed(str(exc))
    said = re.search(r'"message"\s*:\s*"([^"]{3,200})"', text)
    return said.group(1) if said else text[:240] or type(exc).__name__


async def try_model(entry: ModelEntry, *, seconds: float = 60.0, pause: float = 2.0) -> dict[str, Any]:
    """One small request, the way Rimuru makes them: does this model answer, and
    can it call a tool? Everything Rimuru and its clones do goes through tools.
    Free models stumble now and then, so a stumble gets one more try."""
    result: dict[str, Any] = {"ok": False, "model": entry.model, "label": entry.label, "seconds": 0, "problem": ""}
    started = time.perf_counter()
    client = None
    try:
        client = await asyncio.to_thread(make_client, entry, concurrency=1)
        if hasattr(client, "max_attempts"):
            client.max_attempts = 2
        for attempt in (1, 2):
            try:
                answer = await asyncio.wait_for(client.complete(
                    system="You answer by calling the reply tool.",
                    messages=[Msg("user", "Say hello to the person, in one short sentence.")],
                    tools=[_HELLO], max_tokens=200, role="router"), seconds)
                break
            except (asyncio.TimeoutError, ProviderAccountError):
                raise
            except Exception:  # noqa: BLE001 - told below, if it happens twice
                if attempt == 2:
                    raise
                await asyncio.sleep(pause)
                started = time.perf_counter()
        result["seconds"] = round(time.perf_counter() - started, 1)
        if answer.tool_calls:
            result["ok"] = True
        else:
            result["problem"] = ("It answered, but it cannot use tools - and everything Rimuru "
                                 "does goes through tools. Pick another model.")
    except asyncio.TimeoutError:
        result["problem"] = "It did not answer within a minute."
    except ProviderAccountError as exc:
        result["problem"] = exc.plain() + "."
    except Exception as exc:  # noqa: BLE001 - whatever it is, it is told as it came
        result["problem"] = _what_it_said(exc)
    finally:
        closing = getattr(client, "aclose", None)
        if closing is not None:
            with noted("closing a model's connection", logging.DEBUG):
                await closing()
    return result


async def try_setup(setup: ModelSetup) -> list[dict[str, Any]]:
    """Each model that Rimuru and the clones would use, tried once."""
    who: dict[str, list[str]] = {}
    for role, label in (("router", "Rimuru"), ("worker", "The clones")):
        who.setdefault(setup.name_for(role), []).append(label)
    names = [name for name in who if name in setup.models]
    results = await asyncio.gather(*(try_model(setup.models[name]) for name in names))
    return [{**result, "who": " and ".join(who[name]).replace("and The", "and the")}
            for name, result in zip(names, results)]


_NOT_CHAT = ("embed", "tts", "whisper", "dall-e", "image", "audio", "moderation",
             "transcribe", "realtime", "search", "rerank", "ocr")


async def list_models(provider: str, *, key: str = "", base_url: str = "",
                      timeout: float = 15.0) -> tuple[str, list[str]]:
    """Ask a provider which models this key can use: (base_url, model IDs)."""
    preset = PROVIDERS.get(provider)
    if preset is None:
        raise ValueError(f'unknown provider "{provider}"')
    if preset.key_env and not key:
        raise ValueError(f"Enter your {preset.label} key first.")
    if provider == "kimi" and not base_url:
        from . import kimi

        url, ids = await asyncio.to_thread(kimi.discover, key)
        _KIMI_BASE[_fingerprint(key)] = url
    else:
        url = (base_url or preset.base_url).rstrip("/")
        if not url:
            raise ValueError("Enter the server's address first.")
        if preset.kind == "anthropic":
            endpoint = f"{url}/v1/models?limit=100"
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        else:
            endpoint = f"{url}/models"
            headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                response = await http.get(endpoint, headers=headers)
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not reach {url}: {type(exc).__name__}.") from None
        if response.status_code in {401, 403}:
            raise ValueError(f"{preset.label} did not accept that key.")
        if response.status_code != 200:
            raise ValueError(f"{url} answered HTTP {response.status_code}.")
        payload = response.json()
        items = payload.get("data") if isinstance(payload, dict) else payload
        ids = sorted({str(m.get("id")) for m in items or [] if isinstance(m, dict) and m.get("id")})
    chat = [m for m in ids if not any(s in m.lower() for s in _NOT_CHAT)]
    return url, chat or ids


# --------------------------------------------------------------------------- #
# describing
# --------------------------------------------------------------------------- #
def describe(setup: ModelSetup, *, demo_forced: bool, root: Path | None = None) -> dict[str, Any]:
    problems = [] if demo_forced else setup.check()
    try:
        shown = str(setup.path.relative_to(root)) if root else str(setup.path)
    except ValueError:
        shown = str(setup.path)
    active = {}
    for role in ROLES:
        entry = setup.entry_for(role)
        active[role] = None if entry is None else {
            "name": entry.name, "provider": entry.provider,
            "provider_label": entry.label, "model": entry.model,
            "same_as_router": role in {"watchdog", "filter"} and not setup.roles.get(role),
        }
    return {
        "path": shown.replace("\\", "/"),
        "exists": setup.path.exists(),
        "demo": demo_forced or bool(problems),
        "demo_forced": demo_forced,
        "problems": problems,
        "roles": {r: setup.roles.get(r, "") for r in ROLES},
        "presets": [{"id": k, "label": v["label"], "about": v["about"], "router": v["router"], "worker": v["worker"]}
                    for k, v in PRESETS.items()],
        "role_info": [{"id": r, "label": ROLE_INFO[r][0], "hint": ROLE_INFO[r][1],
                       "required": r in REQUIRED} for r in ROLES],
        "active": active,
        "models": [e.to_public() for e in setup.models.values()],
        "providers": [{"id": p.id, "label": p.label, "kind": p.kind, "base_url": p.base_url,
                       "key_env": p.key_env, "needs_key": bool(p.key_env), "hint": p.hint,
                       "key_present": bool(next((os.environ.get(n) for n in (p.key_env, *p.key_aliases)
                                                 if n and os.environ.get(n, "").strip()), ""))
                       if p.key_env else True}
                      for p in PROVIDERS.values()],
    }
