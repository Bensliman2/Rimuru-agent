"""The Worker layer (article s2.2).

Each Worker has an isolated context, a full set of execution tools, and exactly
one task. Workers determine the quality of the output, so they are the sessions
that get the strong model.

Workers never talk to humans. Their reasoning and tool traffic stay inside the
session; conclusions go to the Router, which owns every external reply. That is
what buys concurrency without users being hit by several sessions answering at
once.

Peer Workers do not message each other. When two of them overlap on the same
environment - the same pull request, say - both subscribe to that Thread, so
both see the same events and get the chance to notice and resolve the conflict
themselves (s3.2).
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from ..agent.loop import AgentLoop
import hashlib

from ..agent.tools import ToolRegistry, b, enum, obj, register_recall, s
from ..ids import new_id
from ..sessions import display
from ..models import Job, Session, Task
from .. import persona
from .. import skills as skillbook
from ..safety import (HIDDEN, REFUSED_HIDDEN, REFUSED_SECRET, hidden_instructions, secret_file,
                      warning_for_model)
from .files import LocalFiles, Refused, SecretFile
from .shell import check_packages, deps_dir_for

SYSTEM_PROMPT = """You are a Worker session inside a Super-agent called Rimuru.

You have one task, your own private context, and the tools to actually do it.

WHO YOU TALK TO
  Only the Router. You have no channel to any human, by design. Your reasoning
  and your tool output stay here. When you reach a conclusion, call report() for
  progress worth knowing or finish() when the task is over, and the Router
  decides what a person should hear.

SLOW WORK
  Anything that takes real time goes through start(), which returns immediately
  with a job ID. Do not wait and do not poll: the result is delivered to you as
  a new event when it is ready, and you can keep working in the meantime. Use
  stop() if a job runs away.

EVENTS WHILE YOU WORK
  You are subscribed to the thread this task came from, so follow-ups arrive
  here directly. Read them:
    - if they change the task, adapt and say so in your next report
    - if the requester withdraws it, finish() with outcome=cancelled
    - if they belong to a different task, call handback() so the Router can
      route them, or unsubscribe() if the thread is no longer yours
  Another worker may be subscribed to the same thread. If you see its activity
  colliding with yours, resolve it yourself - do not wait to be coordinated.

FILES ARE SHARED
  Other clones may be changing the same files at the same time. Change files
  with edit_file (an exact, small replacement) whenever you can. write_file
  replaces a whole file: it needs the version from your latest read_file and is
  refused if the file changed since. If a write or edit is refused, read the
  file again and merge - never drop changes someone else made.

FINISHING
  finish() ends the session. Call it once, with an honest summary: what you did,
  what you verified, and anything you could not confirm. If you failed, say so
  with outcome=failed rather than dressing it up.
"""

NO_SHELL_NOTE = """
NO SHELL IN THIS DEPLOYMENT
  Shell execution is switched off. start() still exists, but it only stands in
  for the time a command would take - it runs nothing, and its output says so.
  Do not treat a start() result as evidence that tests passed. To do real work,
  use list_files, read_file and write_file on the workspace, and verify by
  reading the code and the tests carefully.
"""

SANDBOX_NOTE = """
YOU CAN RUN CODE - IN A SANDBOX
  Commands run in an isolated Linux container, your workspace mounted as the
  current directory, 1 GB of memory, no network. Already there: Python 3.12 with
  pytest, requests, httpx, beautifulsoup4, flask, fastapi, uvicorn, jinja2, numpy,
  pandas, matplotlib, pillow, openpyxl, pyyaml and sqlalchemy; Node.js 22 with npm.
  Need something else? install() it (Python from PyPI, Node from npm) - then also
  list it in requirements.txt or package.json, so the person can run the project
  without you. Use start() for anything that takes a while - test suites, scripts
  - and bash() for quick checks. Actually run your tests before you finish, and
  report the real result: if they fail, fix the code or say so.
"""

HOST_NOTE = """
COMMANDS RUN ON THE HOST
  Shell commands run directly on this machine, unsandboxed. Keep them inside the
  workspace and never touch anything outside it.
"""

QUICK_NOTE = """
WORK FAST: A FIRST VERSION FIRST
  Someone is waiting, and every token you write - thinking included - takes
  time. So:
  - Think briefly. Plan the next step in a few lines, act, and look at the
    result. Do not design the whole thing up front.
  - Build the smallest version that does what was asked: as few files as it
    needs, and nothing nobody asked for - no README, icons or config files, and
    no server when a page alone will do. One file is best when one is enough.
  - Write a few focused tests for the core behaviour, not an exhaustive suite.
    Run them, fix what fails, and stop.
  - Write each file once and completely, and make independent writes in the
    same turn.
  - When it works, finish(). In your summary, name up to three improvements
    you left out, so the person can ask for them.
"""

SPECULATIVE_NOTE = """
THIS TASK IS SPECULATIVE
  Nobody asked you for this. You are acting ahead on a signal in the
  environment, spending background compute on the chance it turns out useful.
  Stay completely silent unless you both confirm the problem AND have a simple
  fix. If you cannot, finish() with outcome=discarded and no fanfare. A
  speculative result that is half-finished is worse than no result at all.
"""


class WorkerAgent:
    def __init__(
        self,
        *,
        task: Task,
        session: Session,
        store: Any,
        bus: Any,
        config: Any,
        client: Any,
        on_report: Any,
        on_finish: Any,
        on_handback: Any,
        speculative: bool = False,
        shell: Any = None,
        resume_note: str | None = None,
        root: Path | None = None,
        own_folder: bool = False,
        restore: dict[str, Any] | None = None,
        memories: str = "",
        files: Any = None,
        where: str | None = None,
        guide: str = "",
        skills: list[Any] | None = None,
        forced: list[Any] | None = None,
    ) -> None:
        self.task = task
        # Skills the person forced for this job (/skill_<name>): in full, in the brief.
        self.forced = list(forced or [])
        # The person's skills, as they stood when this clone began: the brief
        # lists them, use_skill() reads one (rimuru/skills.py).
        self.skills = [sk for sk in (skills or []) if sk.enabled]
        if files is not None:
            # A folder on another machine (rimuru/hands): its files and its
            # commands go there; nothing is made on this computer.
            self.files = files
            self.root = Path(str(root or "."))
            self.where = where or str(root)
        else:
            # Its folder: the session's own, or the practice folder for other work.
            self.root = Path(root or config.workspace).resolve()
            self.root.mkdir(parents=True, exist_ok=True)
            self.where = display(self.root)
            self.files = LocalFiles(self.root)
        # A folder the person picked holds their own files, not just Rimuru's.
        self.own_folder = own_folder
        # Packages installed for this folder live outside it (shell.install).
        self.deps = deps_dir_for(config, self.root)
        self.session = session
        self.store = store
        self.bus = bus
        self.config = config
        self.on_report = on_report
        self.on_finish = on_finish
        self.on_handback = on_handback
        self.speculative = speculative
        self.shell = shell
        self._job_tasks: dict[str, asyncio.Task] = {}

        self.registry = ToolRegistry()
        self._register_tools()

        prompt = (
            SYSTEM_PROMPT
            + {"docker": SANDBOX_NOTE, "host": HOST_NOTE}.get(
                getattr(shell, "kind", "simulated"), NO_SHELL_NOTE)
            + QUICK_NOTE
            + (SPECULATIVE_NOTE if speculative else "")
        )
        self.loop = AgentLoop(
            session=session,
            client=client,
            store=store,
            bus=bus,
            config=config,
            system_prompt=prompt,
            registry=self.registry,
            role="worker",
            budget_chars=config.worker_context_budget,
            max_steps=config.worker_max_steps,
            # A worker writes whole files as tool arguments; a 2k cap would
            # truncate the JSON mid-file and break the call.
            max_tokens=config.worker_max_tokens,
        )

        if restore is not None:
            # Resumed after a restart: its own conversation, exactly as it was, and
            # a note on what the restart cut off. No new brief, no summary.
            self.loop.restore(restore)
            self.loop.inject(resume_note or "The app restarted; carry on where you were.",
                             kind="system")
            return

        # The brief goes into the inbox now, at construction, not at launch. A
        # queued worker is already subscribed to its thread, so follow-ups can
        # arrive while it waits; queueing the brief first guarantees it reads
        # what its task is before it reads "any update on that?".
        brief = (
            f"{persona.introduction(session)}\n\n"
            f'Your task is {self.task.id}: "{self.task.title}"\n\n'
            f"{self.task.brief}\n\n"
        )
        if self.task.thread_id and not speculative:
            brief += (
                f"You are subscribed to thread {self.task.thread_id}, so follow-up "
                f"events on it will arrive here directly.\n\n"
            )
        brief += (
            f"Your folder on the person's computer is {self.where}/ (inside the sandbox it is "
            f"/workspace). When you mention files, name them from that folder - for example "
            f"index.html - never /workspace/...\n\n"
        )
        if self.own_folder:
            brief += (
                "This is the person's own folder, picked by them, and it may already hold "
                "their work. Look at what is there before you change anything, build on it "
                "rather than starting over, and never delete or rename a file you did not "
                "create unless they asked for exactly that.\n\n"
            )
        if guide:
            brief += guide
        if self.forced:
            brief += skillbook.for_forced(self.forced)
        others = [sk for sk in self.skills if sk.slug not in {f.slug for f in self.forced}]
        if others:
            brief += skillbook.for_brief(others)
        if memories:
            brief += memories.rstrip() + "\n\n"
        if resume_note:
            brief += resume_note.rstrip() + "\n\n"
        brief += "Begin."
        self.loop.inject(brief, kind="system")

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self.loop.start()

    # ------------------------------------------------------------------ #
    # tools
    # ------------------------------------------------------------------ #
    def _register_tools(self) -> None:
        reg = self.registry
        register_recall(reg)

        reg.add(
            "start",
            "Begin a time-consuming operation in the background. Returns a job ID "
            "immediately - it does NOT wait. The result arrives later as an event. "
            "Use this for builds, test runs, reproductions, long analyses.",
            obj(
                command=s("The command or operation to run"),
                label=s("Short description of what this stage is for", optional=True),
            ),
            self._tool_start,
            background=True,
        )

        reg.add(
            "stop",
            "Stop a background job you started that is taking too long or is no "
            "longer useful.",
            obj(job_id=s("Job ID (job_...)"), reason=s("Why", optional=True)),
            self._tool_stop,
        )

        reg.add(
            "report",
            "Tell the Router something worth knowing before you are finished: real "
            "progress, a blocker, or a question only a human can answer. Do not "
            "narrate routine steps.",
            obj(
                status=enum("Where the task stands",
                            ["progressing", "blocked", "needs_decision", "at_risk"]),
                text=s("The substance, briefly. The Router may pass this to a person."),
            ),
            self._tool_report,
        )

        reg.add(
            "finish",
            "End this task. Call it exactly once. The session stops afterwards.",
            obj(
                summary=s("What you did, what you verified, what you could not confirm"),
                outcome=enum("How it ended",
                             ["done", "failed", "cancelled", "discarded"]),
            ),
            self._tool_finish,
        )

        reg.add(
            "unsubscribe",
            "Stop receiving events from a thread that is no longer part of your task.",
            obj(thread_id=s("Thread ID (thr_...)"), reason=s("Why", optional=True)),
            self._tool_unsubscribe,
        )

        reg.add(
            "subscribe",
            "Also watch another thread that is relevant to your task - a linked pull "
            "request, for instance. Its events will then arrive here too.",
            obj(thread_id=s("Thread ID (thr_...)"), reason=s("Why", optional=True)),
            self._tool_subscribe,
        )

        if self.skills:
            reg.add(
                "use_skill",
                "Read one of the person's skills in full - a how-to they wrote (their code "
                "style, how they deploy, ...). Use it when a skill listed in your brief fits "
                "your job, before you do that part.",
                obj(name=s("The skill's name, as listed in your brief")),
                self._tool_use_skill,
            )

        reg.add(
            "handback",
            "Give an event back to the Router because it belongs to a different "
            "task. The Router will route it afresh.",
            obj(trigger_id=s("Event ID (trg_...)"), reason=s("Why it is not yours")),
            self._tool_handback,
        )

        reg.add(
            "list_files",
            "List files in the workspace, optionally under a sub-directory.",
            obj(path=s("Directory relative to the workspace, default '.'", optional=True)),
            self._tool_list_files,
        )

        reg.add(
            "read_file",
            "Read a file from the workspace.",
            obj(path=s("Path relative to the workspace")),
            self._tool_read_file,
        )

        reg.add(
            "edit_file",
            "Replace an exact piece of text in a file - the safe way to change a file "
            "other clones may also be editing. 'old' must appear exactly once (add "
            "surrounding lines to make it unique) unless replace_all is true.",
            obj(path=s("Path relative to the workspace"), old=s("Exact text to replace"),
                new=s("Replacement text"), replace_all=b("Replace every occurrence")),
            self._tool_edit_file,
        )

        reg.add(
            "write_file",
            "Create a new file, or replace a whole existing one. Replacing needs "
            "base_version from your latest read_file, and is refused if the file "
            "changed since. Prefer edit_file for changes.",
            obj(path=s("Path relative to the workspace"), content=s("Full file contents"),
                base_version=s("Version from read_file (omit only for a new file)",
                               optional=True)),
            self._tool_write_file,
        )

        if getattr(self.shell, "kind", "simulated") in {"docker", "host"}:
            reg.add(
                "bash",
                "Run a shell command in the workspace and wait for it. Prefer start() "
                "for anything slow.",
                obj(command=s("Command to run"), timeout=s("Seconds, default 60", optional=True)),
                self._tool_bash,
            )
        if getattr(self.shell, "kind", "simulated") == "docker":
            reg.add(
                "install",
                "Install packages the sandbox does not have yet: Python packages from PyPI "
                "(ready-made versions only) or Node packages from npm. They are downloaded "
                "in a separate container that cannot see the person's files, and your "
                "commands can use them right after. Also list them in requirements.txt or "
                "package.json.",
                obj(packages=s("Package names, separated by spaces - e.g. 'requests "
                               "flask==3.1.0' or 'express lodash@4'"),
                    kind=enum("Which kind", ["python", "node"])),
                self._tool_install,
            )

    # ------------------------------------------------------------------ #
    # background jobs: the mechanism behind full duplex (s4.1)
    # ------------------------------------------------------------------ #
    async def _tool_start(self, args: dict[str, Any], loop: AgentLoop) -> str:
        command = str(args.get("command") or "").strip()
        label = str(args.get("label") or "").strip()
        if not command:
            return "No command given."

        job = self.store.put_job(Job(
            id=new_id("job"), session_id=self.session.id, command=command,
        ))
        self.session.pending_jobs.append(job.id)
        self.store.touch_session(self.session, current_activity=label or command)

        self._job_tasks[job.id] = loop.track_background(self._run_job(job, loop))

        return (
            f'Started [{job.id}]: "{command}". It is running in the background and '
            f"this call has already returned - keep working. The result will be "
            f"delivered to you as an event."
        )

    async def _run_job(self, job: Job, loop: AgentLoop) -> None:
        try:
            status, output = await self.shell.run(job.command, job_id=job.id, workdir=self.root,
                                                  deps=self.deps)
        except asyncio.CancelledError:
            # stop() was called, or the session is shutting down. The worker
            # asked for this, so no completion event is delivered.
            if job.status == "running":
                self.store.update_job(job, status="stopped", result="stopped",
                                      finished_ts=time.time())
            if job.id in self.session.pending_jobs:
                self.session.pending_jobs.remove(job.id)
            self._job_tasks.pop(job.id, None)
            return
        except Exception as exc:
            status, output = "failed", f"{type(exc).__name__}: {exc}"

        self._job_tasks.pop(job.id, None)
        self.store.update_job(job, status=status, result=output, finished_ts=time.time())
        if job.id in self.session.pending_jobs:
            self.session.pending_jobs.remove(job.id)
        self.store.touch_session(self.session)

        verb = "Job finished" if status == "done" else "Job failed"
        shown = f"`{job.command[:60]}`"
        warning = self._scan(output, f"The output of {shown}", f"the output of {shown}")
        loop.inject(
            f'{warning}{verb} [{job.id}] after {(job.finished_ts or 0) - job.started_ts:.1f}s.\n'
            f'command: {job.command}\n'
            f"output:\n{output[:2000]}",
            kind="job",
        )

    async def _tool_stop(self, args: dict[str, Any], loop: AgentLoop) -> str:
        job_id = str(args.get("job_id") or "").strip()
        job = self.store.jobs.get(job_id)
        if job is None or job.session_id != self.session.id:
            return f'No job "{job_id}" of yours.'
        if job.status != "running":
            return f"{job_id} already {job.status}."
        self.store.update_job(job, status="stopped",
                              result=str(args.get("reason") or "stopped by the worker"),
                              finished_ts=time.time())
        # Actually end the work, not just the bookkeeping.
        if task := self._job_tasks.pop(job_id, None):
            task.cancel()
        if job_id in self.session.pending_jobs:
            self.session.pending_jobs.remove(job_id)
        return f"Stopped {job_id}."

    # ------------------------------------------------------------------ #
    def _real_paths(self, text: str) -> str:
        """The sandbox calls the folder /workspace; the person knows it by its real
        place. Found in the speed benchmark: a result said "open /workspace/todo.html"."""
        # A function, not a string: a Windows path's backslashes are not escapes.
        return re.sub(r"(?<![\w.])/workspace(?=/|\b)", lambda _m: self.where, text)

    async def _tool_report(self, args: dict[str, Any], loop: AgentLoop) -> str:
        status = str(args.get("status") or "progressing")
        text = self._real_paths(str(args.get("text") or "").strip())
        await self.on_report(self, status, text)
        return "Reported to the Router."

    async def _tool_finish(self, args: dict[str, Any], loop: AgentLoop) -> str:
        summary = self._real_paths(str(args.get("summary") or "").strip())
        outcome = str(args.get("outcome") or "done")
        await self.on_finish(self, outcome, summary)
        loop.request_stop(status="done")
        return f"Task closed as {outcome}."

    async def _tool_unsubscribe(self, args: dict[str, Any], loop: AgentLoop) -> str:
        thread_id = str(args.get("thread_id") or "").strip()
        ok = self.store.unsubscribe(thread_id, self.session.id)
        return (
            f"Unsubscribed from {thread_id}; its events will go to the Router again."
            if ok else f"You were not subscribed to {thread_id}."
        )

    async def _tool_subscribe(self, args: dict[str, Any], loop: AgentLoop) -> str:
        thread_id = str(args.get("thread_id") or "").strip()
        if thread_id not in self.store.threads:
            return f'No thread "{thread_id}".'
        self.store.subscribe(thread_id, self.session.id)
        return f"Subscribed to {thread_id}; its events will now arrive here."

    async def _tool_use_skill(self, args: dict[str, Any], loop: AgentLoop) -> str:
        key = str(args.get("name") or "").strip()
        # Read now, from its file: an edit since this clone began counts, and a
        # skill turned off since cannot be used.
        skill = skillbook.get(self.config.skills_dir, key)
        if skill is None or not skill.enabled:
            usable = [sk.slug for sk in skillbook.load_all(self.config.skills_dir) if sk.enabled]
            return (f"No skill called {key!r}."
                    + (f" The skills are: {', '.join(usable)}." if usable else " There are none now."))
        self.store.touch_session(self.session, current_activity=f"reading the skill {skill.name}")
        self.bus.emit("skill.used", session_id=self.session.id, task_id=self.task.id,
                      skill=skill.slug, name=skill.name)
        return (f'SKILL "{skill.name}" - the person wrote this. Follow it for your job unless the job '
                f"says otherwise.\n\n{skill.text}")

    async def _tool_handback(self, args: dict[str, Any], loop: AgentLoop) -> str:
        trigger_id = str(args.get("trigger_id") or "").strip()
        reason = str(args.get("reason") or "not part of this task").strip()
        if trigger_id not in self.store.triggers:
            return f'No event "{trigger_id}".'
        ok = await self.on_handback(self, trigger_id, reason)
        return (
            f"Handed {trigger_id} back to the Router, which will route it afresh."
            if ok else f"Could not hand back {trigger_id}."
        )

    # ------------------------------------------------------------------ #
    # files - through self.files: this computer, or a folder on another machine
    # ------------------------------------------------------------------ #
    async def _tool_list_files(self, args: dict[str, Any], loop: AgentLoop) -> str:
        try:
            found = await self.files.list(str(args.get("path") or "."))
        except Refused:
            return "Refused: that path is outside the workspace."
        except Exception as exc:  # noqa: BLE001 - e.g. the other machine went away
            return f"Could not list the files: {exc}"
        if found is None:
            return f"No such directory: {args.get('path') or '.'}"
        if not found:
            return "(no files)"
        more = f"\n... and {len(found) - 200} more" if len(found) > 200 else ""
        return "\n".join(f"{name}  (holds secrets - cannot be opened)" if secret_file(name) else name
                         for name in found[:200]) + more

    async def _tool_read_file(self, args: dict[str, Any], loop: AgentLoop) -> str:
        raw = str(args.get("path") or "")
        try:
            text = await self.files.read(raw)
            name = self.files.name(raw)
        except SecretFile:
            return REFUSED_SECRET.format(name=raw)
        except Refused:
            return "Refused: that path is outside the workspace."
        except Exception as exc:  # noqa: BLE001
            return f"Could not read {raw}: {exc}"
        if text is None:
            return f"No such file: {raw}"
        warning = self._scan(text, f"This file ({name})", f"{name} (in {self.where}/)")
        return (f"{warning}version: {_version(text)}  (pass as base_version to write_file)\n"
                f"{text[:20000]}")

    def _scan(self, text: str, for_model: str, for_person: str) -> str:
        """Hidden instructions aimed at AI tools in what this clone reads: tell the
        person (system._on_hidden, once per passage) and remind the model."""
        found = hidden_instructions(text)
        if not found:
            return ""
        self.bus.emit("safety.hidden_instructions", session_id=self.session.id,
                      task_id=self.task.id, thread_id=self.task.thread_id,
                      source=for_person, job=self.task.title, found=found)
        return warning_for_model(found, for_model)

    # Versions are content hashes, so ANY change - another clone, or a shell
    # command in the sandbox - makes a stale write fail instead of silently
    # erasing that change (the lost update the evaluation caught).
    async def _tool_write_file(self, args: dict[str, Any], loop: AgentLoop) -> str:
        raw = str(args.get("path") or "")
        content = str(args.get("content") or "")
        base = str(args.get("base_version") or "").strip()
        if HIDDEN in content:                  # a whole-file rewrite would destroy the real key
            return REFUSED_HIDDEN
        try:
            existing = await self.files.read(raw)
        except SecretFile:
            return REFUSED_SECRET.format(name=raw)
        except Refused:
            return "Refused: that path is outside the workspace."
        except Exception as exc:  # noqa: BLE001
            return f"Could not write {raw}: {exc}"
        if existing is not None:
            current = _version(existing)
            if not base:
                return (f"Refused: {raw} already exists. Read it first and pass its version "
                        f"as base_version - or use edit_file for a targeted change. "
                        f"Nothing was written.")
            if base != current:
                return (f"Refused: {raw} changed since you read it - another clone may have "
                        f"edited it. Nothing was written. Read it again, merge your changes "
                        f"into the new content, and retry (or use edit_file).")
        try:
            await self.files.write(raw, content)
        except Exception as exc:  # noqa: BLE001
            return f"Could not write {raw}: {exc}"
        return f"Wrote {len(content)} chars to {raw} (new version: {_version(content)})"

    async def _tool_edit_file(self, args: dict[str, Any], loop: AgentLoop) -> str:
        raw = str(args.get("path") or "")
        old, new = str(args.get("old") or ""), str(args.get("new") or "")
        if HIDDEN in old or HIDDEN in new:
            return REFUSED_HIDDEN
        try:
            text = await self.files.read(raw)
        except SecretFile:
            return REFUSED_SECRET.format(name=raw)
        except Refused:
            return "Refused: that path is outside the workspace."
        except Exception as exc:  # noqa: BLE001
            return f"Could not read {raw}: {exc}"
        if text is None:
            return f"No such file: {raw}. Use write_file to create it."
        if not old:
            return "Refused: 'old' is empty. Give the exact text to replace."
        count = text.count(old)
        if count == 0:
            return (f"Refused: that text is not in {raw} - it may have been changed by "
                    f"another clone. Read the file again and copy the text exactly.")
        if count > 1 and not args.get("replace_all"):
            return (f"Refused: that text appears {count} times in {raw}. Include more "
                    f"surrounding lines so it is unique, or set replace_all.")
        text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        try:
            await self.files.write(raw, text)
        except Exception as exc:  # noqa: BLE001
            return f"Could not write {raw}: {exc}"
        return f"Edited {raw} (new version: {_version(text)})"

    async def _tool_install(self, args: dict[str, Any], loop: AgentLoop) -> str:
        kind = str(args.get("kind") or "python").strip().lower()
        names = [n for n in re.split(r"[\s,]+", str(args.get("packages") or "").strip()) if n]
        problem = check_packages(names, kind)
        if problem:
            return f"Not installed: {problem}"
        self.store.touch_session(self.session, current_activity=f"installing {' '.join(names)}")
        status, output = await self.shell.install(names, kind, self.deps)
        if status != "done":
            return f"Could not install them.\n{output[-1500:]}"
        self.bus.emit("worker.installed", session_id=self.session.id, task_id=self.task.id,
                      packages=names, kind=kind)
        return (f"Installed ({kind}): {' '.join(names)}. Your commands can use them now - and "
                f"list them in {'requirements.txt' if kind == 'python' else 'package.json'}.\n"
                f"{output[-800:]}")

    async def _tool_bash(self, args: dict[str, Any], loop: AgentLoop) -> str:
        command = str(args.get("command") or "").strip()
        if not command:
            return "No command."
        try:
            timeout = float(args.get("timeout") or 60)
        except (TypeError, ValueError):
            timeout = 60.0
        _status, output = await self.shell.run(command, timeout=timeout,
                                               job_id=new_id("job"), workdir=self.root,
                                               deps=self.deps)
        shown = f"`{command[:60]}`"
        return self._scan(output, f"The output of {shown}", f"the output of {shown}") + output


def _version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
