"""Plain-language views of the system, for people who do not use agents.

The technical dashboard shows sessions, tool calls and transcripts. This module
turns the same data into what a non-technical person wants to know: what each
clone is working on, what it is doing right now, what it produced, and whether
its tests pass - with the raw details kept alongside for anyone who clicks in.
"""
from __future__ import annotations

import re
import time
from typing import Any
from .. import persona

PASSED_RE = re.compile(r"(\d+) passed")
FAILED_RE = re.compile(r"(\d+) failed")
ERROR_RE = re.compile(r"(\d+) errors?\b")

# Model-written text (a brief, a report) can carry internal IDs and channel names.
# They mean nothing to the person reading the details page, so they are dropped.
_ID = r"\b(?:trg|thr|wrk|tsk|job|rtr|wdg|stp)_[0-9a-f]{4,}\b"
_LABELLED_ID = re.compile(
    r"\s*\(?\b(?:thread|trigger|task|worker|job|session)(?:[ _]id)?\s*[:=]?\s*" + _ID + r"\)?\.?",
    re.IGNORECASE)
_BARE_ID = re.compile(r"\s*\(?" + _ID + r"\)?")
_JARGON = (
    (re.compile(r"\b(?:on|in|via|from) the (?:direct )?console(?: direct)?(?: chat| line| thread| channel)?\b",
                re.I), "in the chat"),
    (re.compile(r"\bconsole direct(?: chat| line)?\b", re.I), "chat"),
)
_RELAYED = re.compile(r"Relayed by the Router:\n(.+?)(?=\n\n|\nRelayed by the Router:|\Z)", re.S)


def plain(text: Any) -> str:
    """Text for people: the same words, minus IDs and channel jargon."""
    text = str(text or "")
    for pattern, repl in _JARGON:
        text = pattern.sub(repl, text)
    text = _LABELLED_ID.sub("", text)
    text = _BARE_ID.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _lines(text: str) -> int:
    return len((text or "").splitlines()) or (1 if text else 0)


def _short(text: Any, n: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "..."


def test_summary(output: str) -> str | None:
    """Read a pytest summary line, if the output has one."""
    if "passed" not in output and "failed" not in output and "error" not in output:
        return None
    passed = PASSED_RE.findall(output)
    failed = FAILED_RE.findall(output)
    errors = ERROR_RE.findall(output)
    parts = []
    if failed:
        parts.append(f"{failed[-1]} failed")
    if errors:
        parts.append(f"{errors[-1]} errors")
    if passed:
        parts.append(f"{passed[-1]} passed")
    return ", ".join(parts) or None


def describe_call(name: str, args: dict[str, Any]) -> str:
    """One tool call, as a sentence."""
    path = args.get("path") or ""
    if name == "list_files":
        return f"Looked at the files in {path}" if path not in ("", ".") else "Looked around the workspace"
    if name == "read_file":
        return f"Read {path}"
    if name == "write_file":
        return f"Wrote {path} ({_lines(str(args.get('content', '')))} lines)"
    if name == "edit_file":
        return f"Edited {path}"
    if name == "start":
        return f"Started: {_short(args.get('label') or args.get('command'))}"
    if name == "bash":
        return f"Ran {_short(args.get('command'), 70)}"
    if name == "stop":
        return "Stopped a command that was running"
    if name == "report":
        return f"Told Rimuru: {_short(args.get('text'), 140)}"
    if name == "finish":
        return f"Finished: {_short(args.get('summary'), 160)}"
    if name == "recall":
        return "Looked up earlier context"
    if name == "subscribe":
        return "Started following a related conversation"
    if name == "unsubscribe":
        return "Stopped following a conversation"
    if name == "handback":
        return "Handed a message back to Rimuru - it was about something else"
    return name.replace("_", " ").capitalize()


ACTIVITY = {
    "list_files": "Looking through the files",
    "read_file": "Reading the code",
    "write_file": "Writing code",
    "start": "Running something",
    "bash": "Running something",
    "report": "Updating Rimuru",
    "finish": "Wrapping up",
    "recall": "Checking earlier context",
}


def _activity(session, steps, jobs) -> str:
    if session.status == "queued":
        return "Waiting for a free clone"
    if session.status in {"done", "stopped", "failed", "interrupted"}:
        return {"done": "Finished", "stopped": "Stopped", "failed": "Ran into a problem",
                "interrupted": "Interrupted by a restart"}[session.status]
    running = [j for j in jobs if j.status == "running"]
    if running:
        cmd = running[-1].command.lower()
        return "Running the tests" if "test" in cmd else "Running a command"
    last = [c["name"] for s in steps[-1:] for c in s.tool_calls]
    if session.status == "thinking":
        if last and last[-1] in {"read_file", "list_files"}:
            return "Thinking it through"
        return "Thinking"
    if last:
        return ACTIVITY.get(last[-1], "Working")
    return "Getting started"


FINISHED = {"done", "failed", "stopped", "interrupted"}


def _is_test(command: str) -> bool:
    low = (command or "").lower()
    return "pytest" in low or "test" in low


def _stage(session, steps, jobs) -> str:
    """What the clone is doing right now, as one word - it drives the little
    slime's animation (reading, writing, testing...)."""
    status = session.status
    if status == "queued":
        return "waiting"
    if status in FINISHED:
        return status
    running = [j for j in jobs if j.status == "running"]
    if running:
        return "testing" if _is_test(running[-1].command) else "running"
    if status == "tool" and "(" in (session.current_activity or ""):
        name = session.current_activity.split("(", 1)[0]
    elif status == "thinking":
        return "thinking"
    else:
        last = [c for st in steps[-1:] for c in st.tool_calls]
        name = last[-1]["name"] if last else ""
    if name in {"write_file", "edit_file"}:
        return "writing"
    if name in {"read_file", "list_files", "recall"}:
        return "reading"
    if name in {"start", "bash"}:
        return "running"
    return "thinking"


def _files_and_tests(steps, jobs):
    files: list[str] = []
    edited: set[str] = set()
    tests: str | None = None
    for step in steps:
        for call, result in zip(step.tool_calls, step.tool_results + [{}] * len(step.tool_calls)):
            path = (call.get("args") or {}).get("path")
            if call["name"] in {"write_file", "edit_file"} and path:
                if path not in files:
                    files.append(path)
                if call["name"] == "edit_file":
                    edited.add(path)
            if call["name"] == "bash":
                tests = test_summary(str(result.get("output", ""))) or tests
    for job in sorted(jobs, key=lambda j: j.started_ts):
        if job.status not in {"running", "interrupted"}:
            tests = test_summary(job.result) or tests
    return files, edited, tests


def worker_card(store, session, conversation_channels) -> dict[str, Any]:
    task = store.tasks.get(session.task_id or "")
    steps = store.transcript(session.id)
    jobs = [j for j in store.jobs.values() if j.session_id == session.id]
    files, edited, tests = _files_and_tests(steps, jobs)

    origin = store.triggers.get(task.origin_trigger_id or "") if task else None
    thread = store.threads.get(task.thread_id or "") if task else None
    phase = {
        "queued": "waiting", "done": "done", "failed": "failed", "stopped": "stopped",
        "interrupted": "interrupted",
    }.get(session.status, "working")
    if task and task.status == "cancelled" and phase != "interrupted":
        phase = "stopped"
    elif task and task.status == "failed" and phase != "interrupted":
        phase = "failed"

    started = session.created_ts if session.status != "queued" else None
    if task and task.started_ts and task.worker_id == session.id:
        started = task.started_ts
    finished = task.finished_ts if task and task.worker_id == session.id else None
    if phase in FINISHED and not finished:
        finished = session.updated_ts
    stage = _stage(session, steps, jobs)
    if phase in {"stopped", "failed"} and stage not in FINISHED:
        stage = phase
    return {
        "id": session.id,
        **persona.card(session),           # name, persona, persona_line
        "task_id": task.id if task else None,
        "title": (task.title if task else session.title) or "Untitled task",
        "phase": phase,
        "stage": stage,
        "activity": plain(_activity(session, steps, jobs)),
        "asked_by": origin.author if origin else None,
        "from_chat": bool(thread and thread.channel in conversation_channels),
        "started_ts": started or session.created_ts,
        "finished_ts": finished,
        "elapsed": (finished or time.time()) - (started or session.created_ts),
        "files": files,
        "edited": sorted(edited),
        "tests": tests,
        "result": plain(task.result) if task and task.status in {"done", "failed"}
                  and task.worker_id == session.id else "",
        "attempt": task.attempt if task else 1,
        "turns": len(steps),
    }


def worker_stages(store, session) -> list[dict[str, Any]]:
    """Four plain steps of any job - the progress strip on the details page.
    Honest about steps that were skipped: 'Not tested' is shown, not hidden."""
    steps = store.transcript(session.id)
    jobs = [j for j in store.jobs.values() if j.session_id == session.id]
    calls = [c for st in steps for c in st.tool_calls]
    files, _edited, tests = _files_and_tests(steps, jobs)
    read = {(c.get("args") or {}).get("path") for c in calls if c["name"] == "read_file"}
    looked = any(c["name"] in {"read_file", "list_files", "recall"} for c in calls)
    testing_now = any(j.status == "running" and _is_test(j.command) for j in jobs)
    stage = _stage(session, steps, jobs)
    finished = session.status in FINISHED

    tiles = [
        {"key": "look", "label": "Looked at the code", "done": looked,
         "detail": f"Read {len(read)} file{'s' if len(read) != 1 else ''}" if read
         else ("Looked around" if looked else "")},
        {"key": "build", "label": "Made the changes", "done": bool(files),
         "detail": f"Changed {len(files)} file{'s' if len(files) != 1 else ''}" if files else ""},
        {"key": "check", "label": "Tested it", "done": bool(tests),
         "detail": tests or ("Testing now" if testing_now else ""),
         "bad": bool(tests and ("failed" in tests or "error" in tests))},
        {"key": "finish", "label": "Reported back", "done": session.status == "done",
         "detail": {"done": "Done", "failed": "Couldn't finish", "stopped": "Stopped",
                    "interrupted": "Interrupted by a restart"}.get(session.status, "")},
    ]
    now_key = {"reading": "look", "writing": "build", "testing": "check",
               "running": "check"}.get(stage)
    if not finished and now_key is None:
        now_key = next((t["key"] for t in tiles if not t["done"]), "finish")
    for tile in tiles:
        if finished:
            tile["state"] = "done" if tile["done"] else "skipped"
            if not tile["done"] and not tile["detail"]:
                tile["detail"] = {"look": "Didn't need to", "build": "Changed nothing",
                                  "check": "Not tested", "finish": ""}[tile["key"]]
        elif tile["key"] == now_key:
            tile["state"] = "current"
        else:
            tile["state"] = "done" if tile["done"] else "todo"
    return tiles


def _step_kind(step) -> str:
    names = [c["name"] for c in step.tool_calls]
    commands = " ".join(str((c.get("args") or {}).get("command", "")) for c in step.tool_calls)
    if any(n in {"write_file", "edit_file"} for n in names):
        return "write"
    if any(n in {"start", "bash"} for n in names):
        return "test" if _is_test(commands) else "run"
    if any(n in {"read_file", "list_files", "recall"} for n in names):
        return "read"
    if "finish" in names:
        return "finish"
    if "report" in names:
        return "talk"
    if "New event on" in step.injected or "Relayed by the Router" in step.injected:
        return "message"
    if "Job finished" in step.injected or "Job failed" in step.injected:
        return "result"
    return "think"


def worker_timeline(store, session) -> list[dict[str, Any]]:
    """Every step, as sentences, with the raw step kept for 'technical details'."""
    jobs = {j.id: j for j in store.jobs.values() if j.session_id == session.id}
    out: list[dict[str, Any]] = []
    for step in store.transcript(session.id):
        lines: list[str] = []
        if "RESUMING AFTER A RESTART" in step.injected:
            lines.append("Picked the job back up after a restart")
        relayed = _RELAYED.findall(step.injected)
        if "New event on a thread you subscribe to" in step.injected or relayed:
            quoted = re.findall(r'\] \([^)]*\) ([^:\n]+): "([^"]*)"', step.injected)
            if quoted:
                lines += [f"Got a message from {who}: \u201c{_short(what, 120)}\u201d" for who, what in quoted]
            if relayed:
                lines += [f"Rimuru passed on: \u201c{_short(plain(what), 220)}\u201d" for what in relayed]
            if not quoted and not relayed:
                lines.append("Got a message from Rimuru")
        for job_id in re.findall(r"Job (?:finished|failed) \[(job_[0-9a-f]+)\]", step.injected):
            job = jobs.get(job_id)
            if job:
                result = test_summary(job.result)
                verdict = f"tests: {result}" if result else ("succeeded" if job.status == "done" else "failed")
                lines.append(f"Result of \u201c{_short(job.command, 60)}\u201d: {verdict}")
        if "[watchdog]" in step.injected:
            lines.append("The watchdog checked in on it")
        for call, result in zip(step.tool_calls, step.tool_results + [{}] * len(step.tool_calls)):
            sentence = describe_call(call["name"], call.get("args") or {})
            output = str(result.get("output", ""))
            if call["name"] == "bash":
                summary = test_summary(output)
                if summary:
                    sentence += f" - tests: {summary}"
            if output.startswith("Refused"):
                sentence += " - refused, the file had changed; it re-read it"
            lines.append(sentence)
        if not lines and step.text:
            lines.append(_short(step.text, 140))
        out.append({
            "index": step.index,
            "ts": step.ts,
            "kind": _step_kind(step),
            "lines": [plain(line) for line in lines] or ["Thought about the next step"],
            "raw": step.to_dict(),
        })
    return out
