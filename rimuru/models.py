"""The object graph the whole system is built out of.

Six object kinds, each with an ID (see ids.py) so the model can walk between
them via recall():

    Trigger  - one environmental event (chat message, CI result, review comment)
    Thread   - a stream of consecutive Triggers on the same subject (s3.2)
    Task     - a unit of work the Router dispatched
    Session  - a live agent context: the Router, a Worker, or the Watchdog
    Step     - one inference turn in a Session transcript (what the UI drills into)
    Job      - a background tool invocation started with start() (s4.1)
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Filtering verdicts (article s3.1)
# --------------------------------------------------------------------------- #
V_PENDING = "pending"
V_DROPPED_FAST = "dropped_fast"      # killed by programmatic policy, no model call
V_CANDIDATE = "candidate"            # passed Fast Path, awaiting semantic judgement
V_DROPPED_SLOW = "dropped_slow"      # model decided no action was needed
V_ACCEPTED = "accepted"              # routed to a session

# Routing paths (article s3.2)
R_PASSTHROUGH = "passthrough"        # Fast Path: straight to subscribed Worker(s)
R_ROUTER = "router"                  # Slow Path: Router evaluates and dispatches

# Session states a loop never comes back from. "interrupted" means the process
# died underneath it (a crash or a restart) - see RimuruSystem recovery.
TERMINAL = frozenset({"done", "failed", "stopped", "interrupted"})


def from_dict(cls, data: dict[str, Any]):
    """Rebuild a dataclass from its to_dict() form, ignoring derived keys."""
    names = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in names}
    if isinstance(kwargs.get("usage"), dict):
        kwargs["usage"] = Usage(**kwargs["usage"])
    return cls(**kwargs)


@dataclass
class Trigger:
    id: str
    channel: str
    thread_key: str
    author: str
    kind: str                     # message | ci | review | email | timer | human_direct
    content: str
    ts: float = field(default_factory=now)
    thread_id: str | None = None
    reply_to: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    verdict: str = V_PENDING
    verdict_reason: str = ""
    fast_path_ms: float = 0.0
    route: str | None = None
    routed_to: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Thread:
    """Consecutive Triggers under the same IM topic, pull request or email chain."""

    id: str
    channel: str
    key: str
    title: str
    created_ts: float = field(default_factory=now)
    last_ts: float = field(default_factory=now)
    trigger_ids: list[str] = field(default_factory=list)
    # Subscriptions are many-to-many: a Worker may watch several Threads and a
    # Thread may have several subscribed Workers (article s3.2).
    subscribers: list[str] = field(default_factory=list)
    # Chat sessions only (rimuru/sessions.py): the folder the person picked for
    # the session's work, and the one Rimuru made for it when they did not.
    folder: str = ""
    rimuru_folder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Task:
    id: str
    title: str
    brief: str
    origin_trigger_id: str | None = None
    thread_id: str | None = None
    worker_id: str | None = None
    status: str = "queued"        # queued | running | done | failed | cancelled
    result: str = ""
    created_ts: float = field(default_factory=now)
    started_ts: float | None = None
    finished_ts: float | None = None
    attempt: int = 1              # +1 for each automatic resume after a restart
    folder: str = ""              # where its clone worked, even if the session moves later
    resume_mark: int | None = None  # its clone's step count when last resumed
    skills: list[str] = field(default_factory=list)  # skills the person forced (/skill_<name>)

    @property
    def duration(self) -> float | None:
        if self.started_ts is None:
            return None
        return (self.finished_ts or now()) - self.started_ts

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration"] = self.duration
        return d


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Step:
    """One inference turn in a session, with everything the UI wants to show."""

    id: str
    session_id: str
    index: int
    ts: float = field(default_factory=now)
    # What was injected into the context at the start of this turn, and how many
    # Triggers were merged into it. batch_size reproduces the article s7.2 metric.
    injected: str = ""
    batch_size: int = 0
    thinking: str = ""
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["usage"] = self.usage.to_dict()
        return d


@dataclass
class Session:
    id: str
    kind: str                     # router | worker | watchdog
    title: str
    model: str
    status: str = "idle"          # idle | thinking | tool | waiting | done | failed | stopped | interrupted
    task_id: str | None = None
    subscriptions: list[str] = field(default_factory=list)
    created_ts: float = field(default_factory=now)
    updated_ts: float = field(default_factory=now)
    step_count: int = 0
    usage: Usage = field(default_factory=Usage)
    current_activity: str = ""
    # Background jobs started with start() and not yet finished (article s4.1).
    pending_jobs: list[str] = field(default_factory=list)
    # A clone's name and persona key (rimuru/persona.py).
    name: str = ""
    persona: str = ""
    # What a finished clone did (web/friendly.py): worked out once from its
    # steps, so showing it never needs them again.
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["usage"] = self.usage.to_dict()
        return d


@dataclass
class Job:
    """A background tool invocation: start() returns immediately and the result
    arrives later as an injected event. This is what makes the loop full-duplex."""

    id: str
    session_id: str
    command: str
    status: str = "running"       # running | done | failed | stopped
    started_ts: float = field(default_factory=now)
    finished_ts: float | None = None
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Memory:
    """Something Rimuru remembers across sessions and restarts (article s8.1):
    a lasting fact about the person, one of their sessions, or anything else.
    The person can see, change and delete every one (the Memory button, next to the chat)."""

    id: str
    text: str
    about: str = "you"            # you | session | general
    session_key: str = ""         # the chat session it belongs to, when about == "session"
    source: str = "rimuru"        # rimuru (it chose to) | you (the person added it)
    created_ts: float = field(default_factory=now)
    used_ts: float = 0.0
    uses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    """One thing a clone found out, in its session's notebook (rimuru/notebook.py):
    what it is about, what is true, and how the clone knows it."""

    id: str
    scope: str                    # the thread it belongs to: clones on the same thread share it
    subject: str                  # the file, function or question it is about
    claim: str
    evidence: str = ""            # how it is known: a test, a command, the code
    task_id: str = ""
    worker_id: str = ""
    relation: str = "new"         # new | confirms | contradicts | settles
    other: str = ""               # the finding it confirms, contradicts or settles
    status: str = "open"          # open | replaced (by its own author) | settled (by a checked one)
    closed_by: str = ""
    created_ts: float = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
