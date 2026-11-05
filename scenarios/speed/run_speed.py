"""Speed benchmark: how long everyday requests take, end to end, on real models.

    python run.py speed probe                       how fast each model writes
    python run.py speed run --label NAME            the everyday tasks, timed and checked
           [--clone-model ID] [--style quick|thorough] [--tasks web-calculator,todo-page,bug-fix]
    python run.py speed compare                     every run so far, side by side

Each task runs in its own fresh workspace and database (data/speed/<label>/<task>/),
so a benchmark never touches the app's own data, and several can run side by side.
Models and keys come from config/models.toml and .env; --clone-model swaps only the
clones' model, for this run only.

Speed without correctness is worthless, so every task is also checked: the bug fix
against hidden tests, the builds by running their own tests and inspecting the page.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rimuru.config import CONFIG, Config  # noqa: E402
from rimuru.llm import catalog  # noqa: E402
from rimuru.llm.base import Msg  # noqa: E402

OUT = ROOT / "data" / "speed"
SHOPKIT = ROOT / "scenarios" / "eval" / "shopkit"
HIDDEN = ROOT / "scenarios" / "eval" / "acceptance"


def _clone_entry(model_id: str | None = None) -> catalog.ModelEntry:
    """The clones' model entry from config/models.toml, optionally with another model ID."""
    setup = catalog.load(CONFIG.models_path)
    base = setup.entry_for("worker")
    if base is None:
        raise SystemExit("No model is set up for the clones - open the app and click Models.")
    return catalog.ModelEntry(name="speed-clone", provider=base.provider, model=model_id or base.model,
                              base_url=base.base_url, api_key_env=base.api_key_env,
                              max_tokens=base.max_tokens, reasoning=base.reasoning)


# --------------------------------------------------------------------------- #
# probe: how fast does each model write?
# --------------------------------------------------------------------------- #
PROBE_PROMPT = (
    "Write a complete single-file to-do list web page: HTML, CSS and JavaScript inline. "
    "Add, complete and delete items, and keep them in localStorage. Reply with the file only."
)


async def probe(models: list[str] | None) -> list[dict]:
    base = _clone_entry()
    if not models:
        _, models = await catalog.list_models(base.provider, key=base.key(), base_url=base.base_url)
    print(f"Asking {len(models)} {base.label} model(s) to write the same small page...\n")

    async def one(model_id: str) -> dict:
        client = catalog.make_client(_clone_entry(model_id), concurrency=8)
        started = time.perf_counter()
        try:
            r = await client.complete(system="You are a careful software engineer.",
                                      messages=[Msg("user", PROBE_PROMPT)], tools=[],
                                      max_tokens=12000, role="worker")
        except Exception as exc:  # noqa: BLE001 - reported per model
            return {"model": model_id, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        finally:
            if hasattr(client, "aclose"):
                await client.aclose()
        took = time.perf_counter() - started
        out = r.usage.output_tokens or 0
        return {"model": model_id, "seconds": round(took, 1), "output_tokens": out,
                "tokens_per_second": round(out / took, 1) if took else 0,
                "thinking_chars": len(r.thinking or ""), "answer_chars": len(r.text or ""),
                "stop": r.stop_reason}

    rows = await asyncio.gather(*(one(m) for m in models))
    rows.sort(key=lambda r: -(r.get("tokens_per_second") or 0))
    print(f"{'model':<28} {'seconds':>8} {'tokens':>7} {'tok/s':>6} {'thinking':>9} {'answer':>8}")
    for r in rows:
        if "error" in r:
            print(f"{r['model']:<28} {r['error']}")
            continue
        print(f"{r['model']:<28} {r['seconds']:>8} {r['output_tokens']:>7} {r['tokens_per_second']:>6} "
              f"{r['thinking_chars']:>9,} {r['answer_chars']:>8,}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "probe.json").write_text(json.dumps(rows, indent=2))
    return rows


# --------------------------------------------------------------------------- #
# checks: did it actually work?
# --------------------------------------------------------------------------- #
def _files(ws: Path, pattern: str) -> list[Path]:
    skip = {".pytest_cache", "__pycache__", "node_modules", ".git"}
    return [p for p in ws.rglob(pattern) if p.is_file() and not skip & set(p.parts)]


def _junit(targets: list[str], cwd: Path, env: dict | None = None) -> dict:
    xml = Path(tempfile.mkdtemp()) / "r.xml"
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           f"--junit-xml={xml}", *targets], capture_output=True, text=True,
                          cwd=cwd, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})},
                          timeout=300)
    results = {}
    if xml.exists():
        for case in ET.parse(xml).getroot().iter("testcase"):
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            results[case.get("name")] = "passed" if bad is None and case.find("skipped") is None else "failed"
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    return {"ok": proc.returncode == 0, "results": results, "summary": tail[-1] if tail else ""}


def _own_tests(ws: Path) -> dict | None:
    """Run the tests the clone wrote, the way anyone would, in a copy of its work."""
    if not (_files(ws, "test_*.py") + _files(ws, "*_test.py")):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "w"
        shutil.copytree(ws, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        return _junit([], copy)


def _page(ws: Path) -> tuple[Path | None, str]:
    pages = sorted(_files(ws, "*.html"), key=lambda p: -p.stat().st_size)
    return (pages[0], pages[0].read_text(errors="replace")) if pages else (None, "")


def check_web_calculator(ws: Path) -> dict:
    page, html = _page(ws)
    low = html.lower()
    controls = low.count("<button") + low.count('type="button"') + low.count("<input")
    scripted = "<script" in low
    tests = _own_tests(ws)
    ok = bool(page) and controls >= 10 and scripted and (tests is None or tests["ok"])
    return {"ok": ok, "page": str(page.relative_to(ws)) if page else None, "controls": controls,
            "scripted": scripted, "own tests": tests and tests["summary"]}


def check_todo_page(ws: Path) -> dict:
    pages = _files(ws, "*.html")
    page, html = _page(ws)
    low = html.lower()
    external = re.findall(r'(?:src|href)="(?!https?:|#|data:)([^"]+\.(?:js|css))"', html)
    ok = (len(pages) == 1 and "<script" in low and "<input" in low and not external
          and bool(re.search(r"\badd\b", low)))
    return {"ok": ok, "html files": len(pages), "inline script": "<script" in low,
            "has an input": "<input" in low, "needs other files": external,
            "other files written": [str(p.relative_to(ws)) for p in _files(ws, "*") if p.suffix in {".js", ".css"}]}


def check_bug_fix(ws: Path) -> dict:
    run = _junit([str(HIDDEN / "test_bulk.py"), str(HIDDEN / "test_regression.py")], HIDDEN,
                 env={"RIMURU_WORKSPACE": str(ws)})
    wanted = ["test_exactly_ten_gets_five_percent", "test_exactly_twenty_five_gets_ten_percent"]
    regressions = [k for k in run["results"] if k.startswith("test_") and "fifty" not in k
                   and "forty" not in k and k not in wanted]
    fixed = all(run["results"].get(k) == "passed" for k in wanted)
    kept = all(run["results"].get(k) == "passed" for k in regressions)
    return {"ok": fixed and kept, "bug fixed (hidden tests)": fixed,
            "nothing else broke": kept, "own tests": (_own_tests(ws) or {}).get("summary")}


TASKS = {
    "web-calculator": {"ask": "Build a calculator with a web UI", "seed": None,
                       "check": check_web_calculator},
    "todo-page": {"ask": "Make a simple to-do list page in a single HTML file", "seed": None,
                  "check": check_todo_page},
    "bug-fix": {"ask": "The bulk discount looks wrong when someone orders exactly 10 of "
                       "something. Can you fix it?", "seed": SHOPKIT, "check": check_bug_fix},
}


# --------------------------------------------------------------------------- #
# run: one task, end to end, through the real system
# --------------------------------------------------------------------------- #
def _models_file(path: Path, clone_model: str | None) -> None:
    setup = catalog.load(CONFIG.models_path)
    run = catalog.ModelSetup(path=path, roles=dict(setup.roles), models=dict(setup.models))
    if clone_model:
        entry = _clone_entry(clone_model)
        run.models[entry.name] = entry
        run.roles["worker"] = entry.name
    catalog.save(run)


async def run_task(label: str, task_id: str, *, clone_model: str | None, style: str,
                   cap: float, demo: bool = False) -> dict:
    from rimuru.system import RimuruSystem
    from rimuru.web.friendly import test_summary

    task = TASKS[task_id]
    base = OUT / label / task_id
    if base.exists():
        shutil.rmtree(base)
    ws = base / "workspace"
    if task["seed"]:
        shutil.copytree(task["seed"], ws, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    ws.mkdir(parents=True, exist_ok=True)
    _models_file(base / "models.toml", clone_model)

    config = Config()
    config.db_path, config.workspace = base / "rimuru.db", ws
    config.models_path, config.demo, config.provider = base / "models.toml", demo, ""
    config.build_style = style
    system = RimuruSystem(config, speed=1.0)
    if config.is_demo() and not demo:
        raise SystemExit("Models are not ready: " + "; ".join(config.setup.check()))
    await system.start()
    store = system.store

    t0 = time.time()
    await system.human_message(task["ask"])
    quiet_since = None
    while time.time() - t0 < cap:
        await asyncio.sleep(2)
        tasks = list(store.tasks.values())
        router_busy = system.router.session.status in {"thinking", "tool"} or system.router.loop.inbox.qsize()
        working = any(t.status in {"queued", "running"} for t in tasks)
        no_job_yet = not tasks and time.time() - t0 < 90
        if working or router_busy or no_job_yet:
            quiet_since = None
            continue
        quiet_since = quiet_since or time.time()
        if time.time() - quiet_since > 8:
            break
    capped = time.time() - t0 >= cap
    if capped:
        for t in list(store.tasks.values()):
            if t.status in {"queued", "running"}:
                await system.pool.stop(t, reason="speed benchmark time cap")

    # ---- what happened ------------------------------------------------------
    said = [m for m in store.chat if m["role"] == "rimuru"]
    workers = [s for s in store.sessions.values() if s.kind == "worker"]
    steps = [st for w in workers for st in store.transcript(w.id)]
    jobs = [j for j in store.jobs.values()]
    clone_out = sum(st.usage.output_tokens for st in steps)
    clone_secs = sum(st.latency_ms for st in steps) / 1000
    thinking = sum(len(st.thinking or "") for st in steps)
    code = sum(len(str(c["args"].get("content", "")) + str(c["args"].get("new", "")))
               for st in steps for c in st.tool_calls if c["name"] in {"write_file", "edit_file"})
    greens = []
    for st in steps:
        for c, r in zip(st.tool_calls, st.tool_results):
            if c["name"] == "bash":
                verdict = test_summary(str(r.get("output", "")))
                if verdict and "failed" not in verdict and "error" not in verdict:
                    greens.append(st.ts + st.latency_ms / 1000)
    for j in jobs:
        verdict = test_summary(j.result or "")
        if verdict and "failed" not in verdict and "error" not in verdict and j.finished_ts:
            greens.append(j.finished_ts)
    finished = [t.finished_ts for t in store.tasks.values() if t.finished_ts]
    router = store.sessions.get("rtr_main")
    result = {
        "label": label, "task": task_id, "style": style,
        "clone model": workers[0].model if workers else None,
        "router model": router.model if router else None,
        "first reply s": round(said[0]["ts"] - t0, 1) if said else None,
        "first passing tests s": round(min(greens) - t0, 1) if greens else None,
        "done s": round(max(finished) - t0, 1) if finished else None,
        "capped": capped,
        "clones": len(workers), "clone turns": len(steps),
        "clone output tokens": clone_out, "clone model seconds": round(clone_secs, 1),
        "tokens per second": round(clone_out / clone_secs, 1) if clone_secs else None,
        "thinking chars": thinking, "code chars": code,
        "router output tokens": router.usage.output_tokens if router else 0,
        "files": sorted(str(p.relative_to(ws)).replace("\\", "/") for p in _files(ws, "*")
                        if not (task["seed"] and (task["seed"] / p.relative_to(ws)).exists())),
        "last message": said[-1]["text"][:400] if said else "",
    }
    await system.stop()
    result["check"] = task["check"](ws)
    (base / "result.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def _row(r: dict) -> str:
    fmt = lambda v: "-" if v is None else (f"{v / 60:.1f}m" if v >= 60 else f"{v:.0f}s")  # noqa: E731
    return (f"{r['label']:<16} {r['task']:<15} {fmt(r['first reply s']):>6} "
            f"{fmt(r['first passing tests s']):>7} {fmt(r['done s']):>7} "
            f"{r['clone output tokens']:>7,} {r['tokens per second'] or 0:>6} "
            f"{r['thinking chars']:>9,} {r['code chars']:>7,} {'PASS' if r['check']['ok'] else 'FAIL':>5}"
            f"{'  (hit the time cap)' if r['capped'] else ''}")


HEADER = (f"{'run':<16} {'task':<15} {'reply':>6} {'tests ok':>7} {'done':>7} "
          f"{'tokens':>7} {'tok/s':>6} {'thinking':>9} {'code':>7} {'works':>5}")


async def run_all(label: str, task_ids: list[str], clone_model: str | None, style: str,
                  cap: float, demo: bool = False) -> list[dict]:
    rows = []
    for task_id in task_ids:
        print(f"[{label}] {task_id}: \"{TASKS[task_id]['ask']}\"", flush=True)
        rows.append(await run_task(label, task_id, clone_model=clone_model, style=style, cap=cap,
                                   demo=demo))
        print(HEADER + "\n" + _row(rows[-1]), flush=True)
    (OUT / label / "results.json").write_text(json.dumps(rows, indent=2, default=str))
    return rows


def compare() -> None:
    rows = []
    for f in sorted(OUT.glob("*/results.json")):
        rows += json.loads(f.read_text())
    if not rows:
        print("No runs yet: python run.py speed run --label NAME")
        return
    print(HEADER)
    for task in TASKS:
        for r in [r for r in rows if r["task"] == task]:
            print(_row(r))
        print()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python run.py speed")
    parser.add_argument("command", choices=["probe", "run", "compare"])
    parser.add_argument("--models", default="", help="probe: comma-separated model IDs (default: all)")
    parser.add_argument("--label", default="run", help="run: a name for this run")
    parser.add_argument("--clone-model", default=None, help="run: the clones' model ID for this run")
    parser.add_argument("--style", default=None, choices=["quick", "thorough"],
                        help="run: how clones work (default: the configured RIMURU_BUILD_STYLE)")
    parser.add_argument("--tasks", default=",".join(TASKS), help="run: comma-separated tasks")
    parser.add_argument("--cap", type=float, default=25.0, help="run: minutes per task at most")
    parser.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)   # dry run, no AI
    args = parser.parse_args(argv)
    if args.command == "probe":
        asyncio.run(probe([m for m in args.models.split(",") if m] or None))
    elif args.command == "run":
        tasks = [t for t in args.tasks.split(",") if t]
        unknown = [t for t in tasks if t not in TASKS]
        if unknown:
            parser.error(f"unknown task(s): {', '.join(unknown)} - choose from {', '.join(TASKS)}")
        asyncio.run(run_all(args.label, tasks, args.clone_model, args.style or CONFIG.build_style,
                            args.cap * 60, demo=args.demo))
    else:
        compare()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
