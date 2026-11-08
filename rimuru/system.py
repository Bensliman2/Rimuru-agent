"""Assembly of the five modules into one Super-agent.

    s2  two-level architecture      RouterAgent  +  WorkerPool
    s3  event-driven triggers       EventFilter  +  EventRouter
    s4  full-duplex interaction     AgentLoop    +  ChannelRegistry
    s5  compute / state separation  Store        (stateless loops, durable state)
    s6  watchdog                    WatchdogAgent

Nothing here holds state of its own: everything durable lives in the Store, so
the loops can be restarted or replaced without losing the continuity of sessions
and tasks.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from .bus import STORY, Bus, compact
from .channels.base import ChannelRegistry, ConsoleChannel
from .channels.scenario import (
    Scenario,
    build as build_scenario,
    seed_build_workspace,
    seed_workspace,
)
from .channels.sim import GitHubSimChannel, ImSimChannel, SimRunner
from .config import CONFIG, Config
from .ids import new_id
from .llm import build_client, catalog
from .llm.base import METER, Msg
from .models import TERMINAL, Task, Trigger
from .recall import describe_graph
from .connections import Connections
from .hands.hub import HandsHub
from .safety import PASTED_KEY_NOTE, notice_for_person, redact
from . import commands, sessions as chats
from .logs import noted
from . import skills as skillbook
from .sessions import title_from, title_of
from .router.filtering import EventFilter
from .router.routing import EventRouter
from .router.session import RouterAgent
from .store import Store
from .watchdog.session import WatchdogAgent
from .worker.pool import WorkerPool
from .worker.shell import make_shell


class RimuruSystem:
    def __init__(self, config: Config | None = None, *, speed: float = 4.0) -> None:
        self.config = config or CONFIG
        self.config.ensure_dirs()
        # Which model does which job: the person's choice in config/models.toml.
        # Anything missing or unreachable means demo mode, and the app says why.
        self.config.setup = catalog.load(self.config.models_path)
        self._connect_models()
        self.provider_note = self.models_line()
        self.speed = speed
        self.started_ts: float | None = None

        self.bus = Bus()
        self.store = Store(self.config.db_path, bus=self.bus)
        self._counts_cache: tuple[int, float, Any] | None = None
        self._overview_cache: tuple[int, float, dict[str, Any]] | None = None
        self._trimmer: asyncio.Task | None = None
        self._minder: asyncio.Task | None = None
        self._quota_told: dict[str, float] = {}
        # The story so far, and from now on saved as it happens: a restart must
        # not blank the pages.
        self.bus.restore(self.store.recent_events(self.bus.history.maxlen or 2000))
        self.bus.on("*", self._keep_event)

        # --- channels (s4.2) ---
        self.channels = ChannelRegistry(bus=self.bus)
        self.channels.register(ImSimChannel(self.bus))
        self.channels.register(GitHubSimChannel(self.bus))
        self.channels.register(ConsoleChannel(self.bus, self.store))

        # --- worker layer (s2.2) ---
        # Job durations are compressed with the scenario; model latency is not,
        # matching the article's own experimental note.
        # Where workers' commands run: a Docker sandbox when available.
        self.shell, self.shell_note = make_shell(self.config, speed=speed)
        self.pool = WorkerPool(
            store=self.store, bus=self.bus, config=self.config, job_speed=speed,
            shell=self.shell,
        )

        # --- router layer (s2.1) ---
        self.router = RouterAgent(
            store=self.store,
            bus=self.bus,
            config=self.config,
            client=build_client(self.config, "router"),
            pool=self.pool,
            channels=self.channels,
        )
        self.pool.bind_router(self.router)

        # --- filtering and routing (s3.1, s3.2) ---
        self.filter = EventFilter(
            store=self.store,
            bus=self.bus,
            config=self.config,
            semantic_client=build_client(self.config, "filter"),
        )
        self.filter.time_scale = speed
        self.event_router = EventRouter(
            store=self.store,
            bus=self.bus,
            config=self.config,
            event_filter=self.filter,
            inject_router=self._inject_router,
            inject_worker=self._inject_worker,
        )

        # --- watchdog (s6) ---
        self.watchdog = WatchdogAgent(
            store=self.store,
            bus=self.bus,
            config=self.config,
            client=build_client(self.config, "watchdog"),
            pool=self.pool,
            router=self.router,
            speed=speed,
        )

        # Rimuru and the watchdog are long-lived records, reloaded on start: say
        # which model they use now, not the one from the last run.
        self.store.touch_session(self.router.session, model=self.config.model_for("router"))
        self.store.touch_session(self.watchdog.session, model=self.config.model_for("watchdog"))

        # --- local simulation harness ---
        self.sim = SimRunner(bus=self.bus, ingest=self.ingest, speed=speed)

        # When the provider refuses the account (out of credit, key refused): the
        # model cannot speak, so the app itself tells the person - once per session.
        self.blocked: dict[str, dict[str, Any]] = {}
        self._told: set[tuple[str, str]] = set()
        self.bus.on("provider.blocked", self._on_provider_blocked)
        self.bus.on("provider.recovered", self._on_provider_recovered)
        # Hidden instructions aimed at AI tools: the clone ignores them, and the app
        # tells the person they are there - once per passage per session.
        self._hidden_told: set[tuple[str, str]] = set()
        self.bus.on("safety.hidden_instructions", self._on_hidden)
        # Packages a clone downloads are said in the session's chat.
        self.bus.on("worker.installed", self._on_installed)

        # The first conversation predates sessions and was called "direct line";
        # name it like every other session, so its folder has a readable name.
        first = self.store.threads.get(self.store.thread_by_key.get("console:direct", ""))
        if first is not None and first.title == "direct line":
            first.title = title_of(self.store, first)
            self.store._persist_thread(first)

        # Telegram and GitHub, when their tokens are set (rimuru/connections.py).
        self.connections = Connections(self)
        # Other machines a session can work on (rimuru/hands/).
        self.hands = HandsHub(self.config, self.bus)
        self.pool.hands = self.hands

        # Whatever was mid-flight when the process last died.
        self.recovered = self._mark_interrupted()

    # ------------------------------------------------------------------ #
    # models
    # ------------------------------------------------------------------ #
    def _connect_models(self) -> None:
        """Find where each chosen model is served (Kimi's region comes from the
        key). A problem puts Rimuru in demo mode and is shown in the app."""
        setup = self.config.setup
        if self.config.demo_forced() or not setup.ready:
            return
        for role in catalog.ROLES:
            entry = setup.entry_for(role)
            try:
                catalog.base_url_for(entry)
            except Exception as exc:  # noqa: BLE001 - shown to the person as is
                setup.problems.append(f'Could not connect "{entry.name}" ({entry.label}): {exc}')
                return

    def models_line(self) -> str:
        """One line on what is thinking, for the console and the logs."""
        if self.config.demo_forced():
            return "demo mode (started with --demo): no AI model is used"
        problems = self.config.setup.check()
        if problems:
            return f"demo mode - {problems[0]}"
        router = self.config.setup.entry_for("router")
        worker = self.config.setup.entry_for("worker")
        return (f"Rimuru: {router.label} {router.model}  |  "
                f"clones: {worker.label} {worker.model}")

    def setup_needed(self) -> bool:
        """The first start: nothing chosen yet, so the app opens on the setup."""
        setup = self.config.setup
        return (not self.config.demo_forced() and not setup.models and not setup.problems
                and self.store.setting("setup_skipped") != "1")

    async def apply_models(self) -> None:
        """Switch to the models now in config/models.toml, without a restart.
        Rimuru, the watchdog and the filter use them from their next turn and
        new clones start on them; running clones finish on the model they had."""
        self.config.setup = catalog.load(self.config.models_path)
        await asyncio.to_thread(self._connect_models)
        self.router.loop.client = build_client(self.config, "router", speed=self.speed)
        self.watchdog.loop.client = build_client(self.config, "watchdog", speed=self.speed)
        self.filter.semantic_client = build_client(self.config, "filter", speed=self.speed)
        self.store.touch_session(self.router.session, model=self.config.model_for("router"))
        self.store.touch_session(self.watchdog.session, model=self.config.model_for("watchdog"))
        # The demo model gets a pretend shell; a real model gets the sandbox.
        demo = self.config.is_demo()
        if self.shell.kind != "host" and getattr(self.shell, "mock", False) != demo:
            self.shell, self.shell_note = make_shell(self.config, speed=self.speed)
            self.pool.shell = self.shell
        self.provider_note = self.models_line()
        self.bus.emit("models.changed", demo=demo, detail=self.provider_note)

    # ------------------------------------------------------------------ #
    # the provider refuses the account
    # ------------------------------------------------------------------ #
    TOP_UP = {"Kimi (Moonshot)": "platform.moonshot.ai", "Claude (Anthropic)": "console.anthropic.com",
              "OpenAI": "platform.openai.com", "DeepSeek": "platform.deepseek.com"}

    def _chat_of(self, session_id: str) -> str | None:
        """The chat session a loop is working for, if it works for one."""
        if session_id == self.router.session.id:
            thread = self.store.threads.get(self.router.latest_console_thread or "")
        else:
            agent = self.store.sessions.get(session_id)
            task = self.store.tasks.get((agent.task_id if agent else None) or "")
            thread = self.store.threads.get((task.thread_id if task else None) or "")
        if thread is None or thread.channel not in self.config.conversation_channels:
            return None
        return thread.key

    def _on_provider_blocked(self, event: dict[str, Any]) -> None:
        provider = event.get("provider") or "The AI provider"
        self.blocked.setdefault(provider, {"reason": event.get("reason"),
                                           "detail": event.get("detail"), "since": event["ts"]})
        if event.get("role") not in {"router", "worker"}:
            return
        key = self._chat_of(event.get("session_id", ""))
        if key is None or (provider, key) in self._told:
            return
        self._told.add((provider, key))
        if event.get("reason") == "credit":
            where = self.TOP_UP.get(provider)
            text = (f"I can't think right now: {provider} says your account is out of credit. "
                    f"Top it up{f' at {where}' if where else ''}, and I will carry on by myself - "
                    f"no need to ask again.")
        else:
            text = (f"I can't think right now: {provider} is not accepting the API key. Check it "
                    f"with the Models button, and I will carry on by myself.")
        self.store.add_chat("rimuru", text, chat=key, notice="blocked")

    def _on_installed(self, event: dict[str, Any]) -> None:
        key = self._chat_of(event.get("session_id", ""))
        if key is not None:
            kind = "Python" if event.get("kind") == "python" else "Node"
            self.store.add_chat("install", f"{', '.join(event.get('packages') or [])} ({kind})",
                                chat=key)

    def _on_hidden(self, event: dict[str, Any]) -> None:
        found = event.get("found") or []
        key = self._chat_of(event.get("session_id", ""))
        if not found or key is None:
            return              # practice runs, team channels: shown under the hood only
        snippet = found[0]["snippet"]
        if (key, snippet.lower()) in self._hidden_told:
            return
        self._hidden_told.add((key, snippet.lower()))
        if any(m.get("notice") == "hidden" and m.get("chat") == key and snippet in m["text"]
               for m in self.store.chat):
            return              # already told, before a restart
        self.store.add_chat("rimuru", notice_for_person(found, event.get("source") or "a file",
                                                        event.get("job") or ""),
                            chat=key, notice="hidden")

    def _on_provider_recovered(self, event: dict[str, Any]) -> None:
        provider = event.get("provider") or "The AI provider"
        if self.blocked.pop(provider, None) is None:
            return
        told = sorted(k for p, k in self._told if p == provider)
        self._told = {(p, k) for p, k in self._told if p != provider}
        for key in told:
            self.store.add_chat("rimuru", f"{provider} is working again - carrying on where I left off.",
                                chat=key, notice="recovered")

    # ------------------------------------------------------------------ #
    # injection bridges used by the EventRouter
    # ------------------------------------------------------------------ #
    def _inject_router(self, text: str, trigger_id: str | None = None) -> None:
        # Messages a person wrote to Rimuru directly are marked, so they can cut
        # in on a turn that is busy with something else.
        trigger = self.store.triggers.get(trigger_id or "")
        from_person = trigger is not None and (
            trigger.kind == "human_direct" or trigger.channel in self.config.conversation_channels)
        if from_person and trigger.channel in self.config.conversation_channels:
            # "console" in a reply means the session that wrote last.
            self.router.latest_console_thread = trigger.thread_id
            self.router.latest_console_ts = time.time()
        self.router.loop.inject(text, kind="human" if from_person else "event",
                                trigger_id=trigger_id)

    def _inject_worker(self, worker_id: str, text: str, trigger_id: str | None = None) -> bool:
        return self.pool.inject(worker_id, text, kind="event", trigger_id=trigger_id)

    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        self.started_ts = time.time()
        # Rimuru's own conversation, saved after every step, comes back as it was.
        saved = self.store.load_context(self.router.session.id)
        if saved:
            self.router.loop.restore(saved)
        await self._recover(router_restored=bool(saved))
        self.router.start()
        self.watchdog.start()
        await self.connections.start()
        if self.hands.machines:
            await self.hands.listen()
        self._trimmer = asyncio.create_task(self._trim_old_details(), name="trim-old-details")
        self._minder = asyncio.create_task(self._every_minute(), name="every-minute")
        self.bus.emit(
            "system.started",
            demo=self.config.is_demo(),
            router_model=self.config.model_for("router"),
            worker_model=self.config.model_for("worker"),
            max_workers=self.config.max_workers,
            bash_enabled=self.config.enable_bash,
        )

    # ------------------------------------------------------------------ #
    # deleting a session
    # ------------------------------------------------------------------ #
    def _keep_event(self, event: dict[str, Any]) -> None:
        if event["topic"] in STORY:
            self.store.add_event(compact(event))

    async def delete_session(self, key: str, *, delete_folder: bool = False) -> dict[str, Any]:
        """Forget a chat session for good. Its clones are stopped first; its files
        go only when asked, and only from Rimuru's own folder for it - a folder
        the person picked, or one on another machine, is never touched."""
        import shutil

        from .sessions import display, folder_for, folder_kind

        thread = next((t for t in self.store.threads.values()
                       if t.key == key and t.channel in self.config.conversation_channels), None)
        if thread is None:
            raise KeyError(key)
        loops = []
        for task in [t for t in self.store.tasks.values()
                     if t.thread_id == thread.id and t.status in {"queued", "running"}]:
            worker = self.pool.workers.get(task.worker_id or "")
            await self.pool.stop(task, reason="the session was deleted")
            if worker is not None:
                loops.append(worker.loop)
        await asyncio.gather(*(loop.join(timeout=5.0) for loop in loops), return_exceptions=True)

        folder = None
        if delete_folder and folder_kind(thread) in {"made", "new"}:
            path = folder_for(self.config, thread).resolve()
            base = self.config.sessions_dir.resolve()
            if path != base and path.is_relative_to(base) and path.is_dir():
                await asyncio.to_thread(shutil.rmtree, path)
                folder = display(path)

        # News about it that has not reached Rimuru yet is dropped, and a step it
        # is taking right now ends first - otherwise either would bring it back.
        marks = (thread.id, f"{thread.channel} {thread.key}")
        inbox = self.router.loop.inbox
        waiting = []
        while not inbox.empty():
            waiting.append(inbox.get_nowait())
        for item in waiting:
            if not any(m in item.text for m in marks):
                inbox.put_nowait(item)
        for _ in range(150):
            if self.router.session.status not in {"thinking", "tool"}:
                break
            await asyncio.sleep(0.1)

        jobs = [t for t in self.store.tasks.values() if t.thread_id == thread.id]
        clones = [t.worker_id for t in jobs]
        # Events about its clones and jobs name those, not the session.
        story_marks = (*marks, *(c for c in clones if c), *(t.id for t in jobs))
        counts = self.store.delete_session_records(thread.id)
        for clone in clones:
            self.pool.workers.pop(clone or "", None)
            self.pool.launched.discard(clone or "")
        self._forget_in_router(thread)
        self._forget_in_rimurus_turns(marks)
        kept = [e for e in self.bus.history if not any(m in str(e) for m in story_marks)]
        self.bus.history.clear()
        self.bus.history.extend(kept)
        self.store.forget_events(story_marks)
        self.bus.emit("chat.deleted", key=key)
        return {"ok": True, "folder_deleted": folder, **counts}

    def rimurus_turns(self) -> list[tuple[Any, set[str]]]:
        """Each of Rimuru's turns, with the chat sessions it was about.

        A turn begins with the events Rimuru was given (batch_size >= 1); the steps
        after it, reading tool results, belong to the same turn - under the session
        they name, or the turn's when they name none. One turn can be about several
        sessions (it handles whatever arrived together), and some about none - a
        clone's progress, the watchdog, a team chat."""
        conv = self.config.conversation_channels
        keys = {t.id: t.key for t in self.store.threads.values() if t.channel in conv}
        named = {f"{t.channel} {t.key}": t.key for t in self.store.threads.values() if t.channel in conv}
        out, current = [], set()
        for step in self.store.transcript(self.router.session.id, last=2000):   # the page shows the latest
            blob = step.injected + json.dumps(step.tool_calls)
            found = {keys[t] for t in re.findall(r"thr_[0-9a-f]+", blob) if t in keys}
            found |= {named[m] for m in re.findall(r"(?:console|telegram) [A-Za-z0-9_-]+", blob) if m in named}
            if step.batch_size >= 1:
                current = found
            out.append((step, set(found or current)))
        return out

    def _forget_in_rimurus_turns(self, marks: tuple[str, ...]) -> None:
        """Its record of turns (Under the hood -> Rimuru) too: each turn about the
        deleted session, from the event that began it to its last step."""
        gone = "(removed: part of a session the person deleted)"
        redacting = False
        for step in self.store.transcript(self.router.session.id):
            mentions = any(m in json.dumps(step.to_dict()) for m in marks)
            if step.batch_size >= 1:                 # a new turn begins here
                redacting = mentions
            if not (redacting or mentions):
                continue
            step.injected, step.thinking = gone, ""
            step.text = gone if step.text else ""
            step.tool_calls = [{**c, "args": {k: gone for k in (c.get("args") or {})}}
                               for c in step.tool_calls]
            step.tool_results = [{**r, "output": gone} for r in step.tool_results]
            self.store.save_step(step)

    def _forget_in_router(self, thread: Any) -> None:
        """Take a deleted session out of what Rimuru has in mind: each event about
        it, and Rimuru's handling of that event, is replaced by a placeholder.
        The shape of the conversation stays, so the model API still accepts it."""
        marks = (thread.id, f"{thread.channel} {thread.key}")
        gone = "(removed: part of a session the person deleted)"
        about = lambda text: any(m in str(text) for m in marks)  # noqa: E731
        loop, redacting = self.router.loop, False
        out: list[Msg] = []
        for msg in loop.messages:
            content = msg.content
            if msg.role == "user" and isinstance(content, str):
                redacting = about(content)
                out.append(Msg(role="user", content=gone) if redacting else msg)
                continue
            if not (redacting or about(content)):
                out.append(msg)
                continue
            if isinstance(content, str):
                out.append(Msg(role=msg.role, content=gone))
                continue
            blocks = []
            for block in content or []:
                block = dict(block)
                if block.get("type") in {"text", "reasoning"}:
                    block["text"] = gone
                elif block.get("type") == "tool_result":
                    block["content"] = gone
                elif block.get("type") == "tool_use":
                    block["input"] = {k: gone for k in (block.get("input") or {})}
                blocks.append(block)
            out.append(Msg(role=msg.role, content=blocks))
        loop.messages = out
        if self.router.latest_console_thread == thread.id:
            self.router.latest_console_thread = None
        # Saved now, busy or not: the copy on disk must not bring it back after a
        # restart. A turn in progress saves again, from this list, at its next step.
        loop._save(None)

    # ------------------------------------------------------------------ #
    # recovery after a crash or restart (article s5.1: state outlives compute)
    # ------------------------------------------------------------------ #
    def _mark_interrupted(self) -> list:
        """Loops that were running when the process died are gone. Say so in the
        records before anything starts, so nothing shows as working that isn't."""
        for job in list(self.store.jobs.running.values()):
            self.store.update_job(job, status="interrupted", finished_ts=time.time(),
                                  result=(job.result or "") + "\n(interrupted by a restart)")
        for session in self.store.sessions.values():
            if session.kind == "worker" and session.status not in TERMINAL:
                session.pending_jobs = []
                self.store.touch_session(session, status="interrupted", current_activity="")
            elif session.kind != "worker" and session.status != "idle":
                self.store.touch_session(session, status="idle", current_activity="")
        return [t for t in self.store.tasks.values() if t.status in {"queued", "running"}]

    def _progress_of(self, session) -> str:
        from .web.friendly import describe_call

        if session is None:
            return ""
        lines = [
            "    - " + describe_call(call["name"], call.get("args") or {})
            for step in self.store.transcript(session.id)
            for call in step.tool_calls
            if call["name"] in {"write_file", "edit_file", "start", "bash", "report"}
        ]
        return "\n".join(lines[-8:])

    # A job is resumed at most this many times. It is stopped sooner when a
    # resumed clone got nowhere before the next restart: then the job itself may
    # be what keeps bringing the app down.
    MAX_RESUMES = 3

    def _cut_off(self, session) -> list[str]:
        """Commands of this clone that a restart cut off before they finished."""
        if session is None:
            return []
        steps = self.store.transcript(session.id)
        since = steps[-1].ts if steps else 0.0
        return [j.command for j in self.store.jobs.of(session.id)
                if (
                    j.status == "interrupted"
                    or (j.status == "stopped" and j.result == "stopped" and (j.finished_ts or 0) >= since))]

    async def _recover(self, router_restored: bool = False) -> None:
        """Pick interrupted work back up - the same clone, exactly where it stopped,
        when its conversation was saved; tell the person; brief the Router."""
        notes: list[str] = []
        for task in self.recovered:
            old = self.store.sessions.get(task.worker_id or "")
            thread = self.store.threads.get(task.thread_id or "")
            in_chat = thread is not None and thread.channel in self.config.conversation_channels
            steps = len(self.store.transcript(old.id)) if old else 0
            saved = self.store.load_context(old.id) if old else None
            stuck = task.attempt >= 2 and task.resume_mark is not None and steps <= task.resume_mark
            if task.attempt > self.MAX_RESUMES or stuck:
                why = "before it could get anywhere" if stuck else f"{task.attempt} times"
                self.store.update_task(
                    task, status="failed", finished_ts=time.time(),
                    result=f"Interrupted by restarts {why}, so not restarted automatically.")
                said = (f'I was interrupted again while working on "{task.title}" ({why}), so I '
                        f"have stopped it - it may be what keeps bringing me down. Ask me if you "
                        f"still want it.")
                notes.append(f'[{task.id}] "{task.title}" - interrupted again ({why}), stopped')
            elif saved and old is not None:
                cut = self._cut_off(old)
                note = (
                    "RESUMED AFTER A RESTART\n"
                    "  The app restarted while you were working. Your conversation is exactly as\n"
                    "  you left it: carry on from your last step. If your last action was cut off,\n"
                    "  check whether it took effect before relying on it.\n"
                    + ("  These commands were cut off and did not finish - run them again if you\n"
                       "  still need them:\n" + "\n".join(f"    - {c}" for c in cut[:8]) + "\n"
                       if cut else ""))
                self.store.update_task(task, attempt=task.attempt + 1, status="queued",
                                       resume_mark=steps)
                await self.pool.submit(task, thread_id=task.thread_id, resume_note=note,
                                       session=old, restore=saved)
                said = (f'I was restarted while working on "{task.title}", so I picked it back up '
                        f"exactly where I left off.")
                notes.append(f'[{task.id}] "{task.title}" - resumed exactly where it stopped')
            else:
                progress = self._progress_of(old)
                resume = (
                    "RESUMING AFTER A RESTART\n"
                    "  A previous clone was working on this when the system restarted. The\n"
                    "  workspace may already hold part of its work: look at the current files\n"
                    "  before changing anything, and do not redo what is already done.\n"
                    + (f"  What it had done:\n{progress}\n" if progress
                       else "  It had not changed anything yet.\n"))
                self.store.update_task(task, attempt=task.attempt + 1, status="queued")
                await self.pool.submit(task, thread_id=task.thread_id, resume_note=resume)
                said = (f'I was restarted while working on "{task.title}", so I have picked '
                        f"it back up.")
                notes.append(f'[{task.id}] "{task.title}" - picked back up by a new clone')
            if in_chat:
                self.store.add_chat("rimuru", said, chat=thread.key)
            self.bus.emit("task.recovered", task_id=task.id, resumed=task.status != "failed")

        if router_restored:
            # Its conversation is back as it was; it only needs to know what happened.
            if notes:
                self.router.loop.messages.append(Msg(role="user", content=(
                    "[system] The app restarted. Your conversation was saved and is back as it "
                    "was. Work that was in progress:\n" + "\n".join(f"  {n}" for n in notes)
                    + "\n\nThe person has already been told about this in the chat.")))
            return

        # No saved conversation (it predates them): seed the Router with the
        # recent chat and the recovered work, without spending a model call now.
        recent = [m for m in self.store.chat[-10:] if m["role"] in {"user", "rimuru"}]
        if recent or notes:
            convo = "\n".join(f"  {m['role']}: {m['text'][:240]}" for m in recent)
            self.router.loop.messages.append(Msg(role="user", content=(
                "[system] You were just restarted, so your earlier context is gone; use "
                "recall() if you need more.\n\nThe chat so far ended with:\n"
                + (convo or "  (nothing yet)")
                + ("\n\nWork that was in progress:\n" + "\n".join(f"  {n}" for n in notes)
                   + "\n\nThe person has already been told about this in the chat."
                   if notes else ""))))

    def quota(self) -> list[dict[str, Any]]:
        """Free plans with a known hourly limit, and how much this hour has used."""
        if self.config.is_demo() or self.config.setup is None:
            return []
        out, seen = [], set()
        for role in ("router", "worker", "watchdog", "filter"):
            entry = self.config.setup.entry_for(role)
            provider = catalog.PROVIDERS.get(entry.provider)
            if provider is None or not provider.hourly or provider.id in seen:
                continue
            seen.add(provider.id)
            out.append({"provider": provider.label, "limit": provider.hourly,
                        "used": METER.used(entry.base_url or provider.base_url)})
        return out

    def warn_about_quota(self) -> None:
        """Once in a while, in the chat the person is using: before the free limit
        stops the work, not after."""
        for q in self.quota():
            if q["used"] < 0.8 * q["limit"] or time.time() - self._quota_told.get(q["provider"], 0) < 1800:
                continue
            self._quota_told[q["provider"]] = time.time()
            threads = [t for t in self.store.threads.values()
                       if t.channel in self.config.conversation_channels and self.store.chat_of(t.key)]
            if threads:
                latest = max(threads, key=lambda t: self.store.chat_of(t.key)[-1]["ts"])
                self.store.add_chat("rimuru", (
                    f"Heads-up: this hour's free requests are nearly used up ({q['used']} of about "
                    f"{q['limit']}). Work may pause for a while, and carries on by itself when it can."),
                    chat=latest.key, notice="quota")
            self.bus.emit("provider.quota", **q)

    async def _every_minute(self) -> None:
        while True:
            await asyncio.sleep(30)
            with noted("the minute's housekeeping"):
                self.warn_about_quota()
                self.pool.sweep_warm()

    KEEP_DETAILS_DAYS = 30

    def keep_details_days(self) -> int:
        """How long a job keeps every detail of every step (0: for ever)."""
        try:
            return max(0, int(self.store.setting("keep_details_days", str(self.KEEP_DETAILS_DAYS))))
        except ValueError:
            return self.KEEP_DETAILS_DAYS

    async def _trim_old_details(self) -> None:
        """Once at start and once a day: old jobs keep their summary and result,
        and the raw detail of each step stops taking up room."""
        while True:
            with noted("trimming old jobs' details"):
                await asyncio.to_thread(self.store.trim_old_details, self.keep_details_days())
            await asyncio.sleep(86400)

    async def stop(self) -> None:
        for chore in (self._trimmer, self._minder):
            if chore is not None:
                chore.cancel()
        await self.connections.stop()
        await self.hands.stop()
        self.sim.stop()
        self.watchdog.stop()
        self.router.loop.request_stop(status="stopped")
        await self.pool.shutdown()
        self.bus.emit("system.stopped")

        # Give the loops a moment to notice the stop flag and unwind. They are
        # allowed to outlive the store, but letting them land first keeps the
        # final persisted state honest.
        loops = [self.router.loop, self.watchdog.loop] + [
            w.loop for w in self.pool.workers.values()
        ]
        await asyncio.gather(
            *(loop.join(timeout=3.0) for loop in loops), return_exceptions=True
        )
        self.store.close()

    # ------------------------------------------------------------------ #
    async def ingest(self, trigger: Trigger) -> str:
        """The single entry point for every environmental event."""
        return await self.event_router.ingest(trigger)

    async def human_message(
        self,
        text: str,
        *,
        channel: str = "console",
        thread_key: str = "direct",
        author: str = "you",
        skills: list[str] | None = None,
        said: str | None = None,
    ) -> Trigger:
        """A person talking to the agent directly from the dashboard.

        kind=human_direct bypasses the Fast Path's noise filters: a message
        addressed straight at the agent is never chatter. `skills` are skills the
        person forced for it (/skill_<name>); `said` is the message as they typed
        it, when that differs from what Rimuru is given.
        """
        meta: dict[str, Any] = {"thread_title": title_from(text)}   # a session is named after it
        if skills:
            meta["skills"] = list(skills)
        trigger = Trigger(
            id=new_id("trigger"),
            channel=channel,
            thread_key=thread_key,
            author=author,
            kind="human_direct",
            content=text,
            meta=meta,
        )
        if channel in self.config.conversation_channels:
            self.store.add_chat("user", said or text, trigger_id=trigger.id, chat=thread_key)
        await self.ingest(trigger)
        return trigger

    async def person_says(self, text: str, *, channel: str = "console", thread_key: str = "direct",
                          author: str = "you") -> dict[str, Any]:
        """Everything a person types in a session: a command (/status, /init), a
        message with skills forced (/skill_web_pages build a page), or a plain
        message for Rimuru. A command or a refusal is answered at once ("said")."""
        # A key pasted into the chat stops here: not stored, not sent to any AI service.
        text, pasted = redact((text or "").strip())
        warned = {"said": PASTED_KEY_NOTE} if pasted else {}
        if commands.parse(text) is not None:
            return await self.run_command(thread_key, text, channel=channel)
        picked = skillbook.forced(text)
        if picked is None:
            trigger = await self.human_message(text, channel=channel, thread_key=thread_key, author=author)
            if pasted:
                self.store.add_chat("rimuru", PASTED_KEY_NOTE, chat=thread_key, notice="safety")
            return {"ok": True, "trigger_id": trigger.id, **warned}
        names, rest = picked
        found, missing = skillbook.resolve(self.config.skills_dir, names)
        if missing or not rest:
            said = (self._no_such_skill(missing) if missing else
                    f"Write what you want done after it, for example: /{names[0]} build a landing page")
            self.store.add_chat("user", text, chat=thread_key)
            self.store.add_chat("rimuru", said, chat=thread_key, notice="command")
            return {"ok": False, "said": said}
        trigger = await self.human_message(rest, channel=channel, thread_key=thread_key, author=author,
                                           skills=[s.name for s in found], said=text)
        if pasted:
            self.store.add_chat("rimuru", PASTED_KEY_NOTE, chat=thread_key, notice="safety")
        return {"ok": True, "trigger_id": trigger.id, "skills": [s.name for s in found], **warned}

    def _no_such_skill(self, missing: list[str]) -> str:
        usable = [s for s in skillbook.load_all(self.config.skills_dir) if s.enabled]
        mine = (" Yours: " + ", ".join(f"/{skillbook.command_for(s)}" for s in usable[:8]) + "."
                if usable else " You have none yet - add them with the Skills button.")
        return f"There is no skill called {', '.join(repr(m) for m in missing)}.{mine}"

    # ------------------------------------------------------------------ #
    # commands typed in the chat (rimuru/commands.py)
    # ------------------------------------------------------------------ #
    async def run_command(self, key: str, text: str, *, channel: str | None = None) -> dict[str, Any]:
        """A command typed in a session's chat, like /init. It shows in the chat as
        the person typed it, with the answer under it - and costs no call to Rimuru:
        the clone's report reaches Rimuru when it is done, like any job's."""
        channel = channel or ("telegram" if key.startswith("tg-") else "console")
        thread_id = self.store.thread_by_key.get(f"{channel}:{key}")
        thread = self.store.threads.get(thread_id or "") or self.store.get_or_create_thread(
            channel, key, chats.UNNAMED)
        name = commands.parse(text) or ""
        self.store.add_chat("user", text.strip(), chat=key, command=name)
        if name == "init":
            ok, said, clone = await self._command_init(thread)
        elif name == "status":
            ok, said, clone = True, commands.status_for(self, thread), ""
        elif name.startswith("skill_"):
            found, missing = skillbook.resolve(self.config.skills_dir, [name[len("skill_"):]])
            ok, clone = False, ""
            said = (self._no_such_skill(missing) if missing else
                    f"Write what you want done after it, for example: /{name} build a landing page")
        else:
            ok, said, clone = False, (f"There is no /{name} command. Try /status - how this session's "
                                      f"work is going - or /init, which studies this session's folder "
                                      f"and writes RIMURU.md, a guide every clone reads first."), ""
        self.store.add_chat("rimuru", said, chat=key, notice="command")
        self.bus.emit("command.ran", command=name, ok=ok, thread_id=thread.id, clone=clone)
        return {"ok": ok, "said": said, "clone": clone}

    async def _command_init(self, thread: Any) -> tuple[bool, str, str]:
        kind = chats.folder_kind(thread)
        folder = chats.folder_for(self.config, thread)
        if kind == "new":
            return False, ("This session has no folder yet. Pick your project's folder with Change, "
                           "at the top of the chat, then type /init again."), ""
        if kind != "remote" and (not folder.is_dir() or not any(folder.iterdir())):
            return False, (f"{chats.display(folder)} is empty, so there is nothing to study yet. Pick "
                           f"your project's folder with Change, or ask me to build something first."), ""
        if any(t.thread_id == thread.id and t.title == commands.INIT_TITLE
               and t.status in {"running", "queued"} for t in self.store.tasks.values()):
            return False, "A clone is already writing RIMURU.md for this session.", ""
        if thread.title in {"", chats.UNNAMED}:
            # Its first job names a session; a guide is not much of a name.
            thread.title = folder.name if kind != "remote" else "A project on another machine"
            self.store.put_thread(thread)
        task = self.store.put_task(Task(id=new_id("task"), title=commands.INIT_TITLE,
                                        brief=commands.INIT_BRIEF, thread_id=thread.id))
        try:
            worker, started = await self.pool.submit(task, thread_id=thread.id)
        except Exception as exc:  # noqa: BLE001 - e.g. the other machine is offline
            self.store.update_task(task, status="failed", finished_ts=time.time(),
                                   result=f"could not start: {exc}")
            return False, f"I could not start a clone for it: {exc}", ""
        where = "the project's folder on the other machine" if kind == "remote" else chats.display(folder)
        later = "" if started else " All clones are busy, so it starts as soon as one is free."
        return True, (f"{worker.name} is reading {where} to write RIMURU.md: what the project is, how "
                      f"it is organized, how to run and test it, and its conventions. From now on every "
                      f"clone that works here reads it first.{later}"), worker.name or ""

    # ------------------------------------------------------------------ #
    def start_scenario(self, name: str = "team", *, speed: float | None = None,
                       **kwargs: Any) -> Scenario:
        scenario = build_scenario(name, **kwargs)
        if name == "smoke":
            # Fresh, still-buggy copy every run, so the outcome is checkable.
            seed_workspace(self.config.workspace)
        elif name == "build":
            seed_build_workspace(self.config.workspace)
        if speed is not None:
            self.speed = max(speed, 0.01)
            self.pool.job_speed = self.speed
            self.watchdog.speed = self.speed
            if hasattr(self.shell, "speed"):
                self.shell.speed = self.speed
            self.filter.time_scale = self.speed
        self.sim.start(scenario, speed=self.speed)
        return scenario

    # ------------------------------------------------------------------ #
    def _usage_by_kind(self) -> dict[str, dict[str, int]]:
        totals: dict[str, dict[str, int]] = {}
        for session in self.store.sessions.values():
            row = totals.setdefault(session.kind, {"input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0})
            for key in row:
                row[key] += getattr(session.usage, key, 0) or 0
        return totals

    def message_counts(self) -> tuple[dict[str, int], dict[str, int]]:
        """What came in and where it went, from every stored message."""
        if self._counts_cache and self._counts_cache[0] == len(self.store.triggers) \
                and time.time() - self._counts_cache[1] < 1.0:
            return self._counts_cache[2]
        seen = {"seen": 0, "dropped_fast": 0, "dropped_slow": 0, "accepted": 0, "slow_calls": 0}
        went = {"passthrough": 0, "to_router": 0, "direct": 0}
        for t in self.store.triggers.values():
            seen["seen"] += 1
            if t.verdict == "dropped_fast":
                seen["dropped_fast"] += 1
            elif t.verdict == "dropped_slow":
                seen["dropped_slow"] += 1
                seen["slow_calls"] += 1
            elif t.verdict == "accepted" and t.kind != "human_direct" and t.route != "passthrough":
                seen["accepted"] += 1
                seen["slow_calls"] += 1
            if t.route == "passthrough":
                went["passthrough"] += 1
            elif t.route == "router":
                went["to_router"] += 1
            if t.kind == "human_direct":
                went["direct"] += 1
        self._counts_cache = (len(self.store.triggers), time.time(), (seen, went))
        return seen, went

    def overview(self) -> dict[str, Any]:
        """Everything the pages' headers need. Adding it all up has a cost that grows
        with the history, so it is done again only when something has happened."""
        cached = self._overview_cache
        if cached and cached[0] == self.bus._seq and time.time() - cached[1] < 1.0:
            return cached[2]
        result = self._compute_overview()
        self._overview_cache = (self.bus._seq, time.time(), result)
        return result

    def _compute_overview(self) -> dict[str, Any]:
        stats = self.store.stats()
        uptime = (time.time() - self.started_ts) if self.started_ts else 0.0
        return {
            "uptime": round(uptime, 1),
            "demo": self.config.is_demo(),
            "models": {
                role: {"model": self.config.model_for(role),
                       "provider": self.config.provider_label_for(role)}
                for role in catalog.ROLES
            },
            "models_note": self.provider_note,
            "blocked": [{"provider": p, **info} for p, info in self.blocked.items()],
            "cut_ins": self.router.loop.cut_ins,
            # Measured on every cut-in: output already written (about 4 characters a
            # token) and time spent, by a streamed call that then stops.
            "cut_in_waste": {
                "calls": self.router.loop.cut_in_waste["calls"],
                "tokens": self.router.loop.cut_in_waste["chars"] // 4,
                "seconds": round(self.router.loop.cut_in_waste["seconds"], 1),
                "streamed": bool(getattr(self.router.loop.client, "stream", False)),
            },
            "bash_enabled": self.config.enable_bash,
            "shell": {"kind": self.shell.kind, "ready": self.shell.ready,
                      "detail": self.shell.describe()},
            "max_workers": self.config.max_workers,
            "quota": self.quota(),
            "store": stats,
            # Tokens by who used them, added up here so no page needs every session.
            "usage_by_kind": self._usage_by_kind(),
            # Counted from the stored messages: a restart no longer empties "What came in".
            "filter": self.message_counts()[0],
            "routing": {**self.message_counts()[1], "handbacks": self.pool.handbacks},
            "router": {
                "status": self.router.session.status,
                "queued": self.router.loop.inbox.qsize(),
                "steps": self.router.session.step_count,
                "activity": self.router.session.current_activity,
                "spilled_turns": self.router.loop.spilled_turns,
                "errors": self.router.loop.error_count,
            },
            "watchdog": {
                "status": self.watchdog.session.status,
                "ticks": self.watchdog.ticks,
                "interventions": self.watchdog.interventions,
            },
            "sim": self.sim.status(),
            "memories": len(self.store.memories),
        }

    def graph(self) -> dict[str, Any]:
        return describe_graph(self.store)
