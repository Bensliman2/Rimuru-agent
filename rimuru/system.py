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
import time
from typing import Any

from .bus import Bus
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
from .llm.base import Msg
from .models import TERMINAL, Trigger
from .recall import describe_graph
from .sessions import DEFAULT as DEFAULT_SESSION, title_from
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
        self.router.start()
        self.watchdog.start()
        await self._recover()
        self.bus.emit(
            "system.started",
            demo=self.config.is_demo(),
            router_model=self.config.model_for("router"),
            worker_model=self.config.model_for("worker"),
            max_workers=self.config.max_workers,
            bash_enabled=self.config.enable_bash,
        )

    # ------------------------------------------------------------------ #
    # recovery after a crash or restart (article s5.1: state outlives compute)
    # ------------------------------------------------------------------ #
    def _mark_interrupted(self) -> list:
        """Loops that were running when the process died are gone. Say so in the
        records before anything starts, so nothing shows as working that isn't."""
        for job in self.store.jobs.values():
            if job.status == "running":
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

    async def _recover(self) -> None:
        """Pick interrupted work back up once; tell the person; brief the Router."""
        notes: list[str] = []
        for task in self.recovered:
            old = self.store.sessions.get(task.worker_id or "")
            thread = self.store.threads.get(task.thread_id or "")
            in_chat = thread is not None and thread.channel in self.config.conversation_channels
            if task.attempt >= 2:
                # Interrupted twice: maybe the job itself is what brings the
                # process down. Do not loop - stop, and leave it to the person.
                self.store.update_task(
                    task, status="failed", finished_ts=time.time(),
                    result="Interrupted by a restart twice, so not restarted automatically.")
                said = (f'I was interrupted again while working on "{task.title}", so I have '
                        f"stopped it for now. Ask me if you still want it.")
                notes.append(f'[{task.id}] "{task.title}" - interrupted twice, stopped')
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

        # The Router's own working context did not survive the restart. Seed it
        # with the recent conversation and the recovered work - without spending
        # a model call now; it is read as context on the next real turn.
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

    async def stop(self) -> None:
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
    ) -> Trigger:
        """A person talking to the agent directly from the dashboard.

        kind=human_direct bypasses the Fast Path's noise filters: a message
        addressed straight at the agent is never chatter.
        """
        trigger = Trigger(
            id=new_id("trigger"),
            channel=channel,
            thread_key=thread_key,
            author=author,
            kind="human_direct",
            content=text,
            # A new session is named after the first thing the person asked.
            meta={"thread_title": "direct line" if thread_key == DEFAULT_SESSION else title_from(text)},
        )
        if channel in self.config.conversation_channels:
            self.store.add_chat("user", text, trigger_id=trigger.id, chat=thread_key)
        await self.ingest(trigger)
        return trigger

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
    def overview(self) -> dict[str, Any]:
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
            "bash_enabled": self.config.enable_bash,
            "shell": {"kind": self.shell.kind, "ready": self.shell.ready,
                      "detail": self.shell.describe()},
            "max_workers": self.config.max_workers,
            "store": stats,
            "filter": self.filter.describe()["counters"],
            "routing": {**self.event_router.stats(), "handbacks": self.pool.handbacks},
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
            "notes": self.store.notes[-10:],
        }

    def graph(self) -> dict[str, Any]:
        return describe_graph(self.store)
