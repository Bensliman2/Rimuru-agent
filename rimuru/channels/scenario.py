"""Synthetic workloads, modelled on the article's s7.1 experiment.

That experiment drives the system with a stream of group-chat and CI messages:
roughly 300 messages per run, of which about 20 are actual work requests. The
rest is casual chat, follow-ups, cancellations and CI noise. Coding work is
simulated by a tool that merely consumes time, so what is being measured is the
system's scheduling and coordination, not its ability to write code.

Threads are modelled as IM *topics*, which is how the article defines them
(s3.2: "consecutive events on the same GitHub Pull Request, IM topic, or email
chain form an event stream"). Casual chat happens on each channel's general
topic; a work request opens a topic of its own, and follow-ups, status questions
and cancellations land on that topic. Modelling a whole channel as one Thread is
a real bug, not a simplification: the first request in a channel would subscribe
a worker to all of it, and every later request there would pass through to that
worker instead of reaching the Router.
"""
from __future__ import annotations

import random
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PEOPLE = ["alice", "ben", "carla", "dmitri", "eun", "farid", "gina", "hale"]

WORK_REQUESTS = [
    "the CSV exporter crashes on an empty result set - traceback in #logs",
    "can you look at the 500 on /api/reports when the date range spans a year?",
    "parser drops the last row when the file has no trailing newline",
    "auth middleware rejects valid tokens issued in the last second, clock skew I think",
    "the retry logic hammers the upstream - no backoff at all, please fix",
    "please add a --dry-run flag to the migration script",
    "memory leak in the ingest worker, grows without bound over ~6h",
    "timezone handling in the scheduler is wrong for half-hour offsets, looks like a bug",
    "the config loader silently ignores unknown keys, could you make it warn?",
    "search returns duplicates when a document is indexed twice - regression from last week",
    "webhook signature check fails for payloads with unicode",
    "could you review the caching PR? I think the invalidation is racy",
    "pagination cursor breaks if a row is deleted mid-scan",
    "the CLI prints a stack trace instead of a message when offline",
    "rate limiter counts cached responses against the quota, that's a bug",
    "please implement bulk delete for the admin API",
    "docs build fails on the new sphinx version",
    "the healthcheck returns 200 while the DB pool is exhausted - wrong output",
    "upload fails for files over 2GB with a confusing error",
    "test suite is flaky on CI - failing on an ordering dependency",
    "connection leak when a request is cancelled mid-query",
    "metrics endpoint hangs the event loop under load",
]

PASSING_REMARKS = [
    "huh, the exporter did something weird for me earlier too",
    "I think there's an off-by-one in the pagination code, not sure",
    "noticed the logs are full of warnings about the config loader",
    "something's up with the retry path, saw the same request four times",
]

CHATTER = [
    "morning", "coffee run, anyone?", "lol", "+1", "agreed", "nice work on that",
    "thanks!", "shipping it", "brb", "in a meeting, back in 20",
    "did anyone watch the keynote?", "lunch?", "same", "good night all",
    "that explains a lot", "ha, classic", "Friday at last",
    "who's on call this week?", "standup in 5", "the office wifi is terrible today",
    "congrats on the release!", "I'll take a look after lunch",
    "my build has been running for 40 minutes", "new laptop finally arrived",
    "anyone else getting slack notifications twice?",
]

FOLLOW_UPS = [
    "actually it only happens with the v2 format",
    "I can reproduce it locally if that helps",
    "adding the stack trace: ValueError at line 212",
    "this is blocking the release, so fairly urgent",
    "no rush on this one",
    "one more detail - it started after Tuesday's deploy",
    "I tried clearing the cache, no difference",
    "happens on both staging and prod",
]

CANCELLATIONS = [
    "nevermind, it was my local config",
    "cancel that, turns out it's expected behaviour",
    "disregard - duplicate of something already fixed",
    "forget it, I worked around it",
]

STATUS_QUERIES = [
    "any update on that?", "where are we with this?", "status?",
    "how long do you think this will take?", "is anyone looking at this?",
]

REVIEW_COMMENTS = [
    "this allocation looks unnecessary, can it reuse the buffer?",
    "nit: the error message should name the field",
    "does this handle the empty case?",
    "please add a regression test for the reported input",
    "I'd rather we didn't widen the public API for this",
]

CHANNELS = [
    ("im", "#dev-general", "dev-general"),
    ("im", "#platform", "platform"),
    ("im", "#bugs", "bugs"),
]


@dataclass
class ScriptedEvent:
    offset: float
    channel: str
    thread_key: str
    author: str
    kind: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    description: str
    events: list[ScriptedEvent]

    @property
    def duration(self) -> float:
        return self.events[-1].offset if self.events else 0.0

    def summary(self) -> dict[str, Any]:
        intents: dict[str, int] = {}
        for event in self.events:
            intent = event.meta.get("intent", event.kind)
            intents[intent] = intents.get(intent, 0) + 1
        return {
            "name": self.name,
            "description": self.description,
            "events": len(self.events),
            "duration": round(self.duration, 1),
            "by_intent": intents,
        }


def _slug(text: str, index: int) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    meaningful = [w for w in words if len(w) > 3][:3] or words[:3]
    return "-".join(meaningful) + f"-{index}"


class _Builder:
    """Tracks open topics so follow-ups land on the topic they belong to."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.events: list[ScriptedEvent] = []
        self.topics: list[tuple[str, str, str, float]] = []   # channel, key, title, opened_at
        self.counter = 0

    def general(self, offset: float, channel_index: int, author: str, content: str,
                intent: str) -> None:
        channel, chan_key, name = CHANNELS[channel_index]
        self.events.append(ScriptedEvent(
            offset, channel, f"{chan_key}/general", author, "message", content,
            {"thread_title": f"{name} / general", "intent": intent},
        ))

    def open_topic(self, offset: float, author: str, content: str) -> None:
        channel, chan_key, name = self.rng.choice(CHANNELS)
        self.counter += 1
        key = f"{chan_key}/{_slug(content, self.counter)}"
        title = content.split(" - ")[0][:56]
        self.events.append(ScriptedEvent(
            offset, channel, key, author, "message", content,
            {"thread_title": title, "intent": "work_request"},
        ))
        self.topics.append((channel, key, title, offset))

    def on_topic(self, offset: float, content: str, intent: str, *, recent: int = 5,
                 min_age: float = 3.0) -> bool:
        candidates = [t for t in self.topics[-recent:] if offset - t[3] >= min_age]
        if not candidates:
            return False
        channel, key, title, _ = self.rng.choice(candidates)
        self.events.append(ScriptedEvent(
            offset, channel, key, self.rng.choice(PEOPLE), "message", content,
            {"thread_title": title, "intent": intent},
        ))
        if intent == "cancellation":
            self.topics = [t for t in self.topics if t[1] != key]
        return True

    def ci(self, offset: float) -> None:
        failed = self.rng.random() < 0.30
        pr = self.rng.randint(400, 430)
        self.events.append(ScriptedEvent(
            offset, "github", f"pr/{pr}", "ci-bot", "ci",
            f"build {'failed' if failed else 'passed'} on pull request #{pr}"
            + (": 2 tests failing in test_export.py" if failed else ""),
            {"status": "failure" if failed else "success",
             "thread_title": f"pull request #{pr}",
             "intent": "ci_failure" if failed else "ci_green"},
        ))

    def review(self, offset: float) -> None:
        pr = self.rng.randint(400, 430)
        self.events.append(ScriptedEvent(
            offset, "github", f"pr/{pr}", self.rng.choice(PEOPLE), "review",
            self.rng.choice(REVIEW_COMMENTS),
            {"thread_title": f"pull request #{pr}", "intent": "review_comment"},
        ))


def build_team_scenario(
    *,
    seed: int = 7,
    total_events: int = 300,
    work_requests: int = 20,
    mean_gap: float = 4.0,
) -> Scenario:
    """The article's own mix: ~300 events, ~20 of them real work."""
    rng = random.Random(seed)
    b = _Builder(rng)
    offset = 2.0

    request_slots = set(rng.sample(range(4, total_events - 10), work_requests))
    pool = rng.sample(WORK_REQUESTS, min(work_requests, len(WORK_REQUESTS)))
    while len(pool) < work_requests:
        pool.append(rng.choice(WORK_REQUESTS))

    for i in range(total_events):
        offset += max(0.4, rng.expovariate(1.0 / mean_gap))
        author = rng.choice(PEOPLE)

        if i in request_slots:
            b.open_topic(offset, author, pool.pop())
            continue

        roll = rng.random()
        # Follow-ups on a topic that already has work on it: the pass-through
        # path - these should reach the worker and never touch the Router.
        # People reply on a human timescale: nobody asks "any update?" three
        # seconds after posting, so each kind of reply waits a realistic while.
        if roll < 0.12 and b.on_topic(offset, rng.choice(FOLLOW_UPS), "follow_up",
                                      min_age=10.0):
            continue
        if roll < 0.17 and b.on_topic(offset, rng.choice(STATUS_QUERIES), "status_query",
                                      min_age=30.0):
            continue
        if roll < 0.19 and len(b.topics) > 3 and b.on_topic(
            offset, rng.choice(CANCELLATIONS), "cancellation", recent=3, min_age=15.0
        ):
            continue
        if roll < 0.28:
            b.ci(offset)            # mostly green, and green is not news
            continue
        if roll < 0.33:
            b.review(offset)
            continue
        if roll < 0.36:
            # A bug mentioned in passing, nobody asking the agent for anything.
            # This is what speculative execution (s3.4) is meant to pick up.
            b.general(offset, rng.randrange(len(CHANNELS)), author,
                      rng.choice(PASSING_REMARKS), "passing_remark")
            continue
        b.general(offset, rng.randrange(len(CHANNELS)), author,
                  rng.choice(CHATTER), "chatter")

    return Scenario(
        name="team",
        description=(
            f"{total_events} events, {work_requests} of them real work requests, the "
            f"rest chatter, follow-ups, status questions, cancellations, review "
            f"comments and CI. Modelled on the article's s7.1 workload."
        ),
        events=b.events,
    )


def build_burst_scenario(
    *,
    seed: int = 11,
    bursts: int = 6,
    per_burst: int = 16,
) -> Scenario:
    """Decision-heavy bursts, to show the Router merging a backlog (s7.2).

    Within a burst, new requests arrive faster than one Router turn takes, so
    they queue while it is thinking and the next turn takes all of them at once.
    """
    rng = random.Random(seed)
    b = _Builder(rng)
    offset = 2.0

    for _ in range(bursts):
        for _ in range(per_burst):
            offset += rng.uniform(0.03, 0.22)
            roll = rng.random()
            if roll < 0.55:
                b.open_topic(offset, rng.choice(PEOPLE), rng.choice(WORK_REQUESTS))
            elif roll < 0.70 and b.on_topic(offset, rng.choice(STATUS_QUERIES),
                                            "status_query", recent=8, min_age=1.0):
                pass
            else:
                b.general(offset, rng.randrange(len(CHANNELS)), rng.choice(PEOPLE),
                          rng.choice(CHATTER), "chatter")
        offset += 30.0   # quiet gap between bursts

    return Scenario(
        name="burst",
        description=(
            f"{bursts} bursts of {per_burst} events within a few seconds each, "
            f"over half of them new requests. Watch the Router's batch size."
        ),
        events=b.events,
    )


def build_quiet_scenario(*, seed: int = 3, total_events: int = 60) -> Scenario:
    """Almost entirely noise, with two real requests buried in it.

    The correct behaviour is near-silence. Useful for checking the filter is not
    simply admitting everything.
    """
    rng = random.Random(seed)
    b = _Builder(rng)
    offset = 2.0
    work_at = {12, 41}
    requests = rng.sample(WORK_REQUESTS, 2)

    for i in range(total_events):
        offset += rng.uniform(1.0, 5.0)
        if i in work_at:
            b.open_topic(offset, rng.choice(PEOPLE), requests.pop())
        else:
            b.general(offset, rng.randrange(len(CHANNELS)), rng.choice(PEOPLE),
                      rng.choice(CHATTER), "chatter")

    return Scenario(
        name="quiet",
        description=(
            f"{total_events} events, only 2 of which deserve action. The right "
            f"outcome is almost total silence."
        ),
        events=b.events,
    )


def build_smoke_scenario() -> Scenario:
    """A short, cheap scenario for testing against a real model.

    Two requests point at genuine bugs in scenarios/demo_repo (copied into the
    workspace first), so the result can be checked objectively afterwards by
    running that repo's tests. Around them: chatter that should be ignored, a
    green CI run that code should drop, a follow-up that should pass straight
    through to its worker, and a status question.
    """
    events = [
        ScriptedEvent(2, "im", "#dev-general/general", "carla", "message",
                      "morning all", {"thread_title": "dev-general / general",
                                      "intent": "chatter"}),
        ScriptedEvent(5, "github", "pr/431", "ci-bot", "ci",
                      "build passed on pull request #431",
                      {"status": "success", "thread_title": "pull request #431",
                       "intent": "ci_green"}),
        ScriptedEvent(8, "im", "#bugs/csv-export-empty", "alice", "message",
                      "the CSV exporter crashes on an empty result set - the code is "
                      "demo_repo/exporter.py and demo_repo/test_exporter.py reproduces it. "
                      "can you fix it?",
                      {"thread_title": "CSV exporter crashes on empty result",
                       "intent": "work_request"}),
        ScriptedEvent(14, "im", "#dev-general/general", "ben", "message",
                      "coffee run, anyone?", {"thread_title": "dev-general / general",
                                              "intent": "chatter"}),
        ScriptedEvent(20, "im", "#bugs/pagination-skips", "dmitri", "message",
                      "pagination skips an item at every page boundary - see "
                      "demo_repo/paginate.py, demo_repo/test_paginate.py shows the bug. "
                      "please fix",
                      {"thread_title": "pagination skips items", "intent": "work_request"}),
        ScriptedEvent(45, "im", "#bugs/csv-export-empty", "alice", "message",
                      "adding detail: it is an IndexError when rows is an empty list",
                      {"thread_title": "CSV exporter crashes on empty result",
                       "intent": "follow_up"}),
        ScriptedEvent(70, "im", "#bugs/pagination-skips", "eun", "message",
                      "any update on this?",
                      {"thread_title": "pagination skips items", "intent": "status_query"}),
        ScriptedEvent(80, "im", "#dev-general/general", "farid", "message",
                      "lunch?", {"thread_title": "dev-general / general",
                                 "intent": "chatter"}),
    ]
    return Scenario(
        name="smoke",
        description=(
            "8 events for testing a real model: two genuine bugs in demo_repo, a "
            "follow-up, a status question, and noise. Check the outcome by running "
            "the demo repo's tests afterwards."
        ),
        events=events,
    )


CALCULATOR_SPEC = (
    "can you build me a small calculator? Put it in calc/calculator.py with a function "
    "evaluate(expression: str) -> float. It should handle + - * / % and ** with "
    "parentheses, unary minus and decimals, following Python's precedence and "
    "associativity rules. Do NOT use eval or exec - write a real tokenizer and parser. "
    "Raise ValueError for malformed input and ZeroDivisionError for division by zero. "
    "Also add calc/cli.py so that python calc/cli.py \"2 * (3 + 4)\" prints the result, "
    "and pytest tests in calc/test_calculator.py."
)

CONVERTER_SPEC = (
    "separately, I need a unit converter: units/converter.py with "
    "convert(value: float, from_unit: str, to_unit: str) -> float. Length: m, km, cm, "
    "mm, mi, ft, in. Mass: kg, g, lb, oz. Temperature: C, F, K. Raise ValueError for an "
    "unknown unit or for converting between different kinds (kg to m, say). Add pytest "
    "tests in units/test_converter.py."
)


def build_build_scenario() -> Scenario:
    """Ask a real model to build two things at once, to exercise everything.

    Two people ask for two unrelated pieces of software within seconds of each
    other, so two Workers run in parallel on their own threads. Mid-way, a
    follow-up adds a requirement to the calculator; it should pass straight
    through to that worker without the Router ever seeing it, and the hidden
    acceptance tests check the requirement made it into the code. A status
    question and some chatter round it out.
    """
    events = [
        ScriptedEvent(2, "im", "#dev-general/calculator", "alice", "message",
                      CALCULATOR_SPEC,
                      {"thread_title": "build a calculator", "intent": "work_request"}),
        ScriptedEvent(4, "im", "#dev-general/unit-converter", "ben", "message",
                      CONVERTER_SPEC,
                      {"thread_title": "build a unit converter", "intent": "work_request"}),
        ScriptedEvent(7, "im", "#dev-general/general", "carla", "message",
                      "nice, curious how this goes", {"thread_title": "dev-general / general",
                                                      "intent": "chatter"}),
        ScriptedEvent(25, "im", "#dev-general/calculator", "alice", "message",
                      "one more thing for the calculator: please also support floor "
                      "division with //",
                      {"thread_title": "build a calculator", "intent": "follow_up"}),
        ScriptedEvent(75, "im", "#dev-general/unit-converter", "ben", "message",
                      "how is the converter going?",
                      {"thread_title": "build a unit converter", "intent": "status_query"}),
    ]
    return Scenario(
        name="build",
        description=(
            "Two people ask for two programs at once (a calculator and a unit "
            "converter), then a follow-up adds a requirement mid-task. Checked "
            "afterwards against hidden acceptance tests the agent never sees."
        ),
        events=events,
    )


def seed_build_workspace(workspace: Path) -> None:
    """Start the build test from a completely empty workspace.

    Learned the hard way against a real model: clearing only calc/ and units/
    was not enough. An interrupted earlier run had left a converter inside
    demo_repo/, and the next worker found it and reported the work as already
    done. And with demo_repo the only thing in the workspace, workers read
    "implement it in the repository" as "inside demo_repo" and put the code
    there instead of where the spec said. An empty workspace removes both.
    """
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    for child in workspace.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


DEMO_REPO = Path(__file__).resolve().parents[2] / "scenarios" / "demo_repo"


def seed_workspace(workspace: Path) -> Path:
    """Copy a fresh, still-buggy demo_repo into the workspace."""
    target = Path(workspace) / "demo_repo"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        DEMO_REPO, target,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    return target


SCENARIOS = {
    "team": build_team_scenario,
    "burst": build_burst_scenario,
    "quiet": build_quiet_scenario,
    "smoke": build_smoke_scenario,
    "build": build_build_scenario,
}


def build(name: str = "team", **kwargs: Any) -> Scenario:
    factory = SCENARIOS.get(name)
    if factory is None:
        raise ValueError(f"unknown scenario {name!r}; have {', '.join(SCENARIOS)}")
    return factory(**kwargs)
