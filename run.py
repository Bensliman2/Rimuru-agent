#!/usr/bin/env python
"""Rimuru entrypoint.

    python run.py                                  dashboard on http://127.0.0.1:8800
    python run.py --scenario team                  dashboard, scenario started for you
    python run.py bench                            headless run, prints the s7.1-style table
    python run.py bench --scenario smoke --speed 1
                                                   real-model test with a pass/fail verdict
    python run.py --demo                           no AI model at all (offline stand-in)
    python run.py hands --connect http://PC:8810 --code CODE --root FOLDER
                                                   on another machine: let sessions work there

Which model does which job is set in config/models.toml - or with the Models
button in the app. API keys live in .env.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time


def _apply_args(args: argparse.Namespace):
    from rimuru.config import CONFIG

    if args.demo or args.provider in {"mock", "demo"}:
        CONFIG.demo = True
    if args.data:
        # Everything this run keeps - database, workspace, session folders - under
        # one folder, e.g. a demo that never mixes with your real history.
        from pathlib import Path

        base = Path(args.data).resolve()
        CONFIG.db_path = base / "rimuru.db"
        CONFIG.workspace = base / "workspace"
        CONFIG.sessions_dir = base / "sessions"
    if args.workers:
        CONFIG.max_workers = args.workers
    return CONFIG


def serve(args: argparse.Namespace) -> int:
    import uvicorn

    from rimuru.web.app import create_app

    config = _apply_args(args)
    try:
        app = create_app(speed=args.speed, autostart=args.scenario)
    except RuntimeError as exc:
        print(f"\n  cannot start: {exc}\n", file=sys.stderr)
        return 1

    host = args.host or config.host
    port = args.port or config.port

    print(f"\n  Rimuru  ->  http://{host}:{port}")
    print(f"  models: {app.state.system.provider_note}")
    if config.is_demo() and not config.demo_forced():
        print("  Open the app and click Models to choose them "
              f"(or edit {config.models_path.name} in config/).")
    print()

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _settled(system) -> bool:
    """Every task finished and the Router has nothing left to say."""
    tasks = list(system.store.tasks.values())
    if any(t.status in {"queued", "running"} for t in tasks):
        return False
    router = system.router
    return router.loop.inbox.qsize() == 0 and router.session.status in {"idle", "stopped"}


async def _bench(args: argparse.Namespace) -> int:
    from rimuru.system import RimuruSystem

    config = _apply_args(args)
    try:
        system = RimuruSystem(config, speed=args.speed)
    except RuntimeError as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 1

    await system.start()
    scenario = system.start_scenario(args.scenario, speed=args.speed)

    print(f"scenario : {scenario.name} - {scenario.description}")
    print(f"events   : {len(scenario.events)} over "
          f"{scenario.duration / args.speed:.0f}s at {args.speed}x")
    print(f"models   : {system.provider_note}")
    print()

    started = time.monotonic()
    feed_end = scenario.duration / args.speed
    deadline = feed_end + args.drain
    tick = 0
    while True:
        await asyncio.sleep(5)
        tick += 5
        elapsed = time.monotonic() - started
        stats = system.store.stats()
        print(
            f"  t+{elapsed:>5.0f}s  events {stats['triggers_total']:>4}"
            f"  tasks {stats['tasks_by_status']['done']:>3} done /"
            f" {stats['tasks_by_status']['running']:>2} running /"
            f" {stats['tasks_by_status']['queued']:>2} queued"
            f"  router turns {stats['router_steps']:>3}"
            f"  batch {stats['router_avg_batch']:.2f}"
        )
        if elapsed > feed_end + 5 and _settled(system):
            print(f"  settled after {elapsed:.0f}s")
            break
        if elapsed > deadline:
            print(f"  stopped at the {args.drain:.0f}s drain limit with work still open")
            break

    _report(system, config)

    verdict = 0
    if args.scenario in {"smoke", "build"}:
        _show_outcome(system)
        verdict = verify_scenario(args.scenario, config)

    await system.stop()
    return verdict


def _report(system, config) -> None:
    stats = system.store.stats()
    counters = system.filter.counters
    routing = system.event_router.stats()

    def pct(value):
        if value is None:
            return "-"
        return f"{value:.0f}s" if value < 120 else f"{value / 60:.1f} min"

    judged = counters["accepted"] + counters["dropped_slow"]
    admitted = counters["accepted"] + routing["passthrough"]
    seen = max(counters["seen"], 1)

    print("\n" + "=" * 62)
    print("  event funnel")
    print(f"    seen                        {counters['seen']}")
    print(f"    dropped by code             {counters['dropped_fast']}"
          f"   ({counters['dropped_fast'] / seen * 100:.0f}%, no model call)")
    print(f"    survived the Fast Path      {counters['candidates']}")
    print(f"      straight to a worker      {routing['passthrough']}"
          f"   (subscribed, no model call)")
    print(f"      judged by the model       {judged}")
    print(f"        dropped                 {counters['dropped_slow']}")
    print(f"        dispatched by the router{counters['accepted']:>4}")
    print(f"    acted on in total           {admitted}"
          f"   ({admitted / seen * 100:.0f}% of the stream)")
    print("\n  routing")
    print(f"    bypassed the router        {routing['passthrough']}")
    print(f"    reached the router         {routing['to_router']}")
    print(f"    kept out of router window  {stats['spill_size']}")
    if admitted:
        print(f"    share bypassing it         "
              f"{routing['passthrough'] / admitted * 100:.0f}%")
    print(f"    handed back by workers     {system.pool.handbacks}")
    print("\n  tasks")
    print(f"    completed            {stats['tasks_by_status']['done']}")
    print(f"    failed               {stats['tasks_by_status']['failed']}")
    print(f"    cancelled            {stats['tasks_by_status']['cancelled']}")
    print(f"    still running/queued {stats['tasks_by_status']['running']}"
          f" / {stats['tasks_by_status']['queued']}")
    print(f"    request -> result    p50 {pct(stats['task_p50'])} / p95 {pct(stats['task_p95'])}")
    print("\n  watchdog")
    print(f"    ticks                {system.watchdog.ticks}")
    print(f"    interventions        {system.watchdog.interventions}")
    print("\n  said to people")
    print(f"    replies sent         {len(system.channels.outbox)}")
    print(f"    truncated at the cap {sum(1 for m in system.channels.outbox if m.truncated)}")
    print("\n  router session")
    print(f"    turns                {stats['router_steps']}")
    print(f"    average batch size   {stats['router_avg_batch']:.2f}")
    print(f"    peak batch size      {stats['router_max_batch']}")
    print("\n  tokens (all sessions)")
    print(f"    input                {stats['usage']['input_tokens']:,}")
    print(f"    cache reads          {stats['usage']['cache_read_tokens']:,}")
    print(f"    output               {stats['usage']['output_tokens']:,}")
    print("=" * 62)
    if config.is_demo():
        print("  NOTE: run in demo mode. Latencies and token counts are")
        print("  simulated, so these numbers illustrate the mechanism rather")
        print("  than measure a model.")
    print()


def _show_outcome(system) -> None:
    print("  what the agent said to people:")
    for message in system.channels.outbox:
        print(f"    [{message.channel} {message.thread_key}] {message.text}")
    print()
    for task in system.store.all_tasks():
        print(f"  task {task.id} [{task.status}] {task.title}")
        if task.result:
            print(f"      {task.result[:300]}")
    print()


def _pytest(label: str, targets: list[str], *, cwd: str, env: dict | None = None) -> bool:
    import os

    print(f"  {label}")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets],
        capture_output=True, text=True, cwd=cwd, env={**os.environ, **(env or {})},
    )
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    failures = [ln for ln in lines if ln.startswith(("FAILED", "ERROR"))]
    for line in failures[:12] + lines[-1:]:
        print(f"    {line}")
    return proc.returncode == 0


def verify_scenario(name: str, config=None) -> int:
    """The objective check: judge the work by tests, not by what the agent says."""
    from pathlib import Path

    from rimuru.config import CONFIG

    config = config or CONFIG
    workspace = Path(config.workspace)
    root = Path(__file__).resolve().parent

    if name == "smoke":
        import shutil
        import tempfile

        repo = workspace / "demo_repo"
        pristine = root / "scenarios" / "demo_repo"
        # The agent can edit the tests in its workspace - and real models do,
        # usually to add coverage. A verdict on tests the agent controls proves
        # nothing, so grade the agent's code against the ORIGINAL tests.
        with tempfile.TemporaryDirectory(dir=str(workspace.parent)) as tmp:
            for src in repo.glob("*.py"):
                if not src.name.startswith("test_"):
                    shutil.copy(src, tmp)
            for test in pristine.glob("test_*.py"):
                shutil.copy(test, tmp)
            passed = _pytest(
                "original tests (the agent could not change these) against its code",
                [tmp], cwd=tmp,
            )
        _pytest("the workspace's tests, including anything the agent added", [str(repo)],
                cwd=str(repo))
        verdict = "PASS - the bugs are fixed" if passed else "FAIL - tests still fail"
        print(f"\n  VERDICT: {verdict}\n")
        return 0 if passed else 2

    if name == "build":
        hidden = sorted(str(p) for p in (root / "scenarios" / "acceptance").glob("test_*.py"))
        accepted = _pytest(
            "hidden acceptance tests (the agent never saw these)", hidden,
            cwd=str(root), env={"RIMURU_WORKSPACE": str(workspace)},
        )
        own = sorted(str(p) for d in ("calc", "units") for p in (workspace / d).glob("test_*.py"))
        if own:
            _pytest("the agent's own tests", own, cwd=str(workspace))
        else:
            print("  the agent's own tests: none were written")

        cli = workspace / "calc" / "cli.py"
        if cli.exists():
            proc = subprocess.run([sys.executable, str(cli), "2 * (3 + 4)"],
                                  capture_output=True, text=True, cwd=str(workspace))
            output = (proc.stdout or proc.stderr).strip()
            print(f'  CLI: python calc/cli.py "2 * (3 + 4)" -> {output[:80]!r}'
                  f"{'  ok' if output.strip() in {'14', '14.0'} else '  (expected 14)'}")
        else:
            print("  CLI: calc/cli.py was not created")

        verdict = "PASS - both programs meet the spec" if accepted else "FAIL - see above"
        print(f"\n  VERDICT: {verdict}\n")
        return 0 if accepted else 2

    print(f"no verification defined for scenario {name!r}")
    return 1


def bench(args: argparse.Namespace) -> int:
    return asyncio.run(_bench(args))


def trace(args: argparse.Namespace) -> int:
    """Reconstruct one task's journey through the system from the database."""
    import json
    import sqlite3
    from pathlib import Path

    from rimuru.config import CONFIG

    db_path = Path(args.db) if args.db else CONFIG.db_path
    if not db_path.exists():
        print(f"no database at {db_path}", file=sys.stderr)
        return 1
    db = sqlite3.connect(db_path)

    def rows(sql, *params):
        return [json.loads(r[0]) for r in db.execute(sql, params)]

    tasks = rows("SELECT data FROM tasks ORDER BY created_ts")
    if not tasks:
        print("no tasks in that database")
        return 1
    task = next((t for t in tasks if t["id"] == args.task), None) if args.task else tasks[0]
    if task is None:
        print(f"no task {args.task}; have: {', '.join(t['id'] for t in tasks)}")
        return 1

    triggers = {t["id"]: t for t in rows("SELECT data FROM triggers")}
    thread_triggers = sorted(
        (t for t in triggers.values() if t.get("thread_id") == task["thread_id"]),
        key=lambda t: t["ts"],
    )
    worker = task.get("worker_id")
    router_steps = rows("SELECT data FROM steps WHERE session_id='rtr_main' ORDER BY idx")
    worker_steps = rows("SELECT data FROM steps WHERE session_id=? ORDER BY idx", worker)
    jobs = rows("SELECT data FROM jobs WHERE session_id=? ORDER BY started_ts", worker)

    events: list[tuple[float, str, str]] = []
    for t in thread_triggers:
        who = f'{t["author"]}: "{t["content"][:70]}"'
        events.append((t["ts"], "event arrives", f'{t["id"]} on {t["channel"]} {t["thread_key"]}  {who}'))
        verdict = f'{t["verdict"]} - {t["verdict_reason"]}'
        if t.get("route") == "passthrough":
            events.append((t["ts"] + 0.0001, "pass-through",
                           f'straight to {", ".join(t["routed_to"])}; the router never sees it'))
        elif t.get("route") == "router":
            events.append((t["ts"] + 0.0001, "filtered", verdict))
            events.append((t["ts"] + 0.0002, "routed", "to the Router (no worker owns this thread yet)"))
        else:
            events.append((t["ts"] + 0.0001, "filtered", verdict))

    thread_ids = {t["id"] for t in thread_triggers}
    for s in router_steps:
        mentions = [i for i in thread_ids if i in s["injected"]]
        about_task = task["id"] in s["injected"] or any(
            c["name"] == "reply" and c["args"].get("thread_id") == task["thread_id"]
            for c in s["tool_calls"]
        )
        if not (mentions or about_task):
            continue
        calls = "; ".join(
            f'{c["name"]}({", ".join(f"{k}={str(v)[:40]}" for k, v in c["args"].items())})'
            for c in s["tool_calls"]
        ) or "no tool calls"
        merged = f'{s["batch_size"]} event(s) merged, ' if s["batch_size"] > 1 else ""
        events.append((s["ts"], f'router turn {s["index"]}',
                       f'{merged}{s["latency_ms"]:.0f}ms -> {calls}'))

    for s in rows("SELECT data FROM steps WHERE session_id='wdg_main' ORDER BY idx"):
        for c in s["tool_calls"]:
            if c["args"].get("session_id") == worker:
                events.append((s["ts"] + s["latency_ms"] / 1000, f'watchdog {c["name"]}',
                               c["args"].get("text") or c["args"].get("reason", "")))

    events.append((task["created_ts"], "task created",
                   f'{task["id"]} -> worker {worker}'))
    for s in worker_steps:
        calls = "; ".join(
            f'{c["name"]}({", ".join(f"{k}={str(v)[:40]}" for k, v in c["args"].items())})'
            for c in s["tool_calls"]
        ) or "no tool call - " + (s["text"] or "")[:60]
        events.append((s["ts"], f'worker turn {s["index"]}', calls))
    for j in jobs:
        if j.get("finished_ts"):
            took = j["finished_ts"] - j["started_ts"]
            events.append((j["finished_ts"], "job completes",
                           f'{j["id"]} "{j["command"]}" {j["status"]} after {took:.1f}s'
                           f' -> injected back as an event'))
    if task.get("finished_ts"):
        events.append((task["finished_ts"], "task closed",
                       f'{task["status"]}: {task["result"][:90]}'))

    events.sort(key=lambda e: e[0])
    origin = events[0][0]
    print(f'\nTRACE {task["id"]}  "{task["title"]}"   (db: {db_path})\n')
    for ts, what, detail in events:
        print(f"  +{ts - origin:7.2f}s  {what:<16} {detail}")
    print()
    return 0


def main() -> int:
    import logging

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if sys.argv[1:2] == ["hands"]:
        # The remote hands, on another machine (rimuru/hands/daemon.py).
        from rimuru.hands.daemon import main as hands_main

        return hands_main(sys.argv[2:])
    if sys.argv[1:2] == ["speed"]:
        # How long everyday requests take, end to end (scenarios/speed/run_speed.py).
        # It has its own options, so it gets the command line as it is.
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent / "scenarios" / "speed" / "run_speed.py"
        spec = importlib.util.spec_from_file_location("run_speed", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Rimuru super-agent")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=["serve", "bench", "trace", "verify", "eval", "speed"])
    parser.add_argument("task", nargs="?", default=None,
                        help="trace: the task id to follow (default: the first task); "
                             "verify: the scenario to check (smoke | build)")
    parser.add_argument("--db", default=None, help="trace: database to read")
    parser.add_argument("--scenario", default=None,
                        help="team | burst | quiet | smoke (bench defaults to team)")
    parser.add_argument("--speed", type=float, default=4.0,
                        help="time compression for message intervals and job durations")
    parser.add_argument("--demo", action="store_true",
                        help="use no AI model at all - an offline stand-in")
    parser.add_argument("--provider", default=None, choices=["mock", "demo"],
                        help=argparse.SUPPRESS)      # older spelling of --demo
    parser.add_argument("--workers", type=int, default=None, help="max concurrent workers")
    parser.add_argument("--data", default=None,
                        help="keep the database, workspace and session folders under this folder")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--drain", type=float, default=None,
                        help="bench: max seconds to keep running after the last event")
    args = parser.parse_args()

    if args.command == "trace":
        return trace(args)
    if args.command == "verify":
        return verify_scenario(args.task or "build")
    if args.command == "eval":
        # The brutal end-to-end evaluation (scenarios/eval/run_eval.py).
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent / "scenarios" / "eval" / "run_eval.py"
        spec = importlib.util.spec_from_file_location("run_eval", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main([args.task] if args.task else [])
    if args.command == "bench":
        args.scenario = args.scenario or "team"
        if args.drain is None:
            args.drain = 600.0 if args.scenario == "smoke" else 90.0
        return bench(args)
    return serve(args)


if __name__ == "__main__":
    sys.exit(main())
