"""Worker pool: admission, subscription, reporting back, handback, stopping.

Concurrency lives here. Each Worker gets its own session, its own context and
its own task, and the only path from a Worker to a human runs through the
Router - so several tasks can be in flight without users seeing a crowd of
sessions all answering at once.

Admission control: past the concurrency limit a task is queued, not refused.
Its session and its Thread subscription are created at once, so follow-ups that
arrive while it waits still reach it, and sit in its inbox until it starts.
"""
from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from typing import Any

from .. import commands, memory, persona
from .. import skills as skillbook
from ..context import inline_trigger
from ..ids import new_id
from ..llm import build_client
from ..models import TERMINAL, Session, Task
from ..hands import remote
from ..hands.remote import RemoteFiles, RemoteShell
from ..sessions import claim_folder, is_session, name_session
from ..safety import hidden_instructions
from .files import LocalFiles
from .session import WorkerAgent



FOLLOW_UP = """A FOLLOW-UP, FOR YOU
  You finished your last job a moment ago, and there is more to do: this is a new
  job, in the same folder. Your conversation is as you left it. Files may have
  changed since - read one again before you edit it.

Your task is {id}: "{title}"

{brief}

"""


class WorkerPool:
    def __init__(
        self,
        *,
        store: Any,
        bus: Any,
        config: Any,
        job_speed: float = 1.0,
        shell: Any = None,
    ) -> None:
        self.store = store
        self.bus = bus
        self.config = config
        self.job_speed = job_speed
        self.shell = shell
        self.workers: dict[str, WorkerAgent] = {}
        self.backlog: deque[WorkerAgent] = deque()
        self.launched: set[str] = set()
        self.handbacks = 0
        self.router: Any = None            # set once the Router exists
        # While the app shuts down, a clone's loop ending is not its job ending:
        # the job stays "running" so the next start resumes it (system._recover).
        self.shutting_down = False
        # Per channel, set by the connections (rimuru/connections.py): where a
        # job works - GitHub: a copy of the repository - and what happens when it
        # is done - GitHub: a draft pull request; Telegram: the files are sent.
        self.preparers: dict[str, Any] = {}
        self.finishers: dict[str, Any] = {}
        # Other machines a session may work on (rimuru/hands/hub.py), set by the system.
        self.hands: Any = None

        # Every way a worker loop can end - finish(), a stop, the step cap, a
        # crash - funnels through here, so tasks never get stuck as "running"
        # and a freed slot always goes to the next queued task.
        bus.on("session.finished", self._on_session_finished)

    # ------------------------------------------------------------------ #
    def bind_router(self, router: Any) -> None:
        self.router = router

    @property
    def live(self) -> list[WorkerAgent]:
        """Workers whose loop is actually running (queued ones excluded)."""
        return [
            w for sid, w in self.workers.items()
            if sid in self.launched and w.session.status not in TERMINAL
        ]

    def get(self, worker_id: str) -> WorkerAgent | None:
        return self.workers.get(worker_id)

    # ------------------------------------------------------------------ #
    async def submit(
        self,
        task: Task,
        *,
        thread_id: str | None = None,
        speculative: bool = False,
        resume_note: str | None = None,
        session: Session | None = None,
        restore: dict[str, Any] | None = None,
        continue_from: Session | None = None,
    ) -> tuple[Session, bool]:
        """Create a Worker for a task. Returns (session, started_now).

        With session and restore: the same clone again, after a restart, taking
        up its saved conversation exactly where it stopped (system._recover)."""
        if session is not None:
            session.model = self.config.model_for("worker")
            session.status = "queued"
            self.store.put_session(session)
        else:
            session_id = new_id("worker")
            # A name no clone at work has, and a persona (rimuru/persona.py).
            at_work = [w.session for w in self.workers.values() if w.session.status not in TERMINAL]
            name, character = (
                (continue_from.name, continue_from.persona) if continue_from is not None else
                persona.pick(session_id, (w.name for w in at_work), (w.persona for w in at_work)))
            session = self.store.put_session(Session(
                id=session_id,
                kind="worker",
                title=task.title,
                model=self.config.model_for("worker"),
                status="queued",
                task_id=task.id,
                name=name,
                persona=character,
            ))

        thread = self.store.threads.get(thread_id or task.thread_id or "")
        picked = str(getattr(thread, "folder", "") or "") if thread is not None else ""
        if task.folder:                  # resumed after a restart: the same folder as before
            folder = task.folder
        elif thread is not None and (prepare := self.preparers.get(thread.channel)):
            folder = str(await prepare(thread, task))
        else:
            if is_session(thread, self.config):
                name_session(self.store, thread, task.title, task.id)
            folder = picked if remote.parse(picked) else str(
                claim_folder(self.config, self.store, thread, task.title))
        if not task.folder:
            self.store.update_task(task, folder=folder)

        # A folder on another machine (rimuru/hands): files and commands go there.
        files = where = None
        shell = self.shell
        root: Any = Path(folder)
        if (on := remote.parse(folder)) is not None and self.hands is not None:
            machine_id, root = on
            info = self.hands.machines.get(machine_id, {})
            files = RemoteFiles(self.hands, machine_id, root)
            shell = RemoteShell(self.hands, machine_id, root, docker=bool(info.get("docker")))
            if not info.get("docker") and info.get("host_commands"):
                shell.kind = "host"
            where = f"{self.hands.name(machine_id)}:{root}"
        # The project's own guide (RIMURU.md from /init, or AGENTS.md / CLAUDE.md):
        # read first, so a fresh clone knows how the project works. A resumed
        # clone already has it in its conversation.
        guide = handoff = ""
        if continue_from is not None:
            restore = self._closed_conversation(continue_from)
            self.store.drop_context(continue_from.id)       # one follow-up carries it on
            resume_note = FOLLOW_UP.format(id=task.id, title=task.title, brief=task.brief)
            if task.skills:
                resume_note += skillbook.for_forced(skillbook.resolve(self.config.skills_dir, task.skills)[0])
        if restore is None:
            found = await commands.project_guide(files or LocalFiles(root))
            if found is not None:
                name, text = found
                guide = commands.guide_for_brief(name, text, bool(hidden_instructions(text)))
            handoff = self._handoff(thread, task)
        worker = WorkerAgent(
            task=task,
            session=session,
            store=self.store,
            bus=self.bus,
            config=self.config,
            client=build_client(self.config, "worker"),
            on_report=self._on_report,
            on_finish=self._on_finish,
            on_handback=self.handback,
            speculative=speculative,
            shell=shell,
            resume_note=resume_note,
            root=root,
            own_folder=bool(picked and picked == folder) or bool(
                picked and not remote.parse(picked) and Path(picked) == Path(folder)),
            restore=restore,
            memories=memory.for_clone(self.store.memories.values(),
                                      thread.key if is_session(thread, self.config) else ""),
            files=files,
            where=where,
            guide=guide,
            handoff=handoff,
            continues=continue_from is not None,
            skills=skillbook.load_all(self.config.skills_dir),
            forced=skillbook.resolve(self.config.skills_dir, task.skills)[0] if task.skills else [],
        )
        self.workers[session.id] = worker
        self.store.update_task(task, worker_id=session.id, status="queued")

        # Subscribe to the originating Thread, so every follow-up on it passes
        # straight through without the Router spending a turn (s3.2).
        #
        # Speculative work does not subscribe. It is background work nobody
        # asked for, and it must not capture a live conversation's traffic -
        # especially a channel's general topic, where most of it is chatter.
        #
        # Nor does it subscribe to a conversation channel - the user's own chat
        # with Rimuru. That chat is one continuous thread: if a worker owned it,
        # everything the user typed next would go to that worker instead of to
        # Rimuru. There, the Router keeps the conversation and relays follow-ups
        # itself (article s2.2 on single-page personal agents).
        thread = self.store.threads.get(thread_id or "")
        conversational = thread is not None and thread.channel in self.config.conversation_channels
        if thread_id and not speculative and not conversational:
            self.store.subscribe(thread_id, session.id)

        if len(self.live) < self.config.max_workers:
            self._launch(worker)
            return session, True

        self.backlog.append(worker)
        self.bus.emit(
            "worker.queued", session_id=session.id, task_id=task.id,
            title=task.title, position=len(self.backlog),
        )
        return session, False

    def _launch(self, worker: WorkerAgent) -> None:
        self.launched.add(worker.session.id)
        self.store.update_task(worker.task, status="running", started_ts=time.time())
        self.store.touch_session(worker.session, status="idle")
        self.bus.emit(
            "worker.spawned", session_id=worker.session.id, task_id=worker.task.id,
            title=worker.task.title, thread_id=worker.task.thread_id,
            speculative=worker.speculative,
        )
        worker.start()

    def _launch_next(self) -> None:
        while self.backlog and len(self.live) < self.config.max_workers:
            worker = self.backlog.popleft()
            if worker.session.status in TERMINAL:
                continue           # cancelled while it waited
            self._launch(worker)

    # ------------------------------------------------------------------ #
    def inject(self, worker_id: str, text: str, *, kind: str = "event",
               trigger_id: str | None = None) -> bool:
        worker = self.workers.get(worker_id)
        if worker is None or worker.loop.stopped:
            return False
        if worker.session.status in TERMINAL:
            return False
        worker.loop.inject(text, kind=kind, trigger_id=trigger_id)
        return True

    # ------------------------------------------------------------------ #
    def _where(self, task: Task) -> tuple[str, str]:
        thread = self.store.threads.get(task.thread_id or "")
        if thread is None:
            return "none", "none"
        return thread.id, f"{thread.channel} {thread.key}"

    async def _on_report(self, worker: WorkerAgent, status: str, text: str) -> None:
        """A Worker reporting in. Goes to the Router, never to a human."""
        task = worker.task
        self.bus.emit(
            "worker.report", session_id=worker.session.id, task_id=task.id,
            status=status, text=text,
        )
        if worker.speculative and status not in {"needs_decision", "blocked"}:
            # Speculative work stays silent until it has something worth saying.
            return
        if self.router:
            thread_id, channel = self._where(task)
            self.router.inject(
                f'Worker [{worker.session.id}] on task [{task.id}] "{task.title}" '
                f"reports status={status}\n"
                f"thread: {thread_id}\nchannel: {channel}\n\n{text}\n\n"
                f"Decide whether anyone needs to hear this. Routine progress "
                f"needs no reply.",
            )

    # ------------------------------------------------------------------ #
    # follow-ups: the clone that just finished carries on
    # ------------------------------------------------------------------ #
    def warm_clone(self, thread_id: str | None) -> Session | None:
        """The clone that just finished a job in this chat session, if that was
        recent enough and its conversation is still kept."""
        thread = self.store.threads.get(thread_id or "")
        if not is_session(thread, self.config):
            return None
        done = [t for t in self.store.tasks_of(thread_id) if t.status == "done" and t.worker_id and t.finished_ts]
        if not done:
            return None
        last = max(done, key=lambda t: t.finished_ts)
        if time.time() - last.finished_ts > self.config.warm_clone_seconds:
            return None
        session = self.store.sessions.get(last.worker_id)
        return session if session is not None and self.store.load_context(session.id) else None

    def _kept_for_a_follow_up(self, worker: WorkerAgent) -> bool:
        """A job done in a person's chat: its clone's conversation is kept for a
        while (sweep_warm), in case the next job follows on from it."""
        thread = self.store.threads.get(worker.task.thread_id or "")
        return worker.task.status == "done" and not worker.speculative and is_session(thread, self.config)

    def _closed_conversation(self, session: Session) -> dict[str, Any]:
        """A finished clone's conversation, every tool call of its last turn answered."""
        saved = dict(self.store.load_context(session.id) or {"messages": []})
        last = (saved["messages"] or [{}])[-1]
        calls = [b for b in last.get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use"] \
            if last.get("role") == "assistant" and isinstance(last.get("content"), list) else []
        saved["pending"] = [{"type": "tool_result", "tool_use_id": b.get("id", ""),
                             "content": "Done - that job is closed."} for b in calls] or None
        return saved

    def sweep_warm(self) -> int:
        """Finished clones' conversations are kept only for the follow-up window."""
        dropped = 0
        for session_id in self.store.context_ids():
            session = self.store.sessions.get(session_id)
            if session is None or (session.status == "done" and session.kind == "worker"
                                   and time.time() - session.updated_ts > self.config.warm_clone_seconds):
                self.store.drop_context(session_id)
                dropped += 1
        return dropped

    def _handoff(self, thread: Any, task: Task) -> str:
        """What earlier clones did in this session, for a new one: without it, all
        it knows of them is what the files and the brief happen to show."""
        if not is_session(thread, self.config):
            return ""
        from ..web import friendly

        jobs = [t for t in self.store.tasks_of(thread.id) if t.id != task.id and t.status in {"done", "failed"}][-5:]
        lines = []
        for job in jobs:
            clone = self.store.sessions.get(job.worker_id or "")
            files = friendly._summary(self.store, clone)["files"][:8] if clone is not None else []
            who = f", by {persona.of(clone)[0]}" if clone is not None else ""
            lines.append(f'- [{job.id}] "{job.title}"{who}: {job.status}. '
                         + " ".join((job.result or "").split())[:280]
                         + (f" Files: {', '.join(files)}." if files else ""))
        if not lines:
            return ""
        return ("EARLIER IN THIS SESSION - jobs already done here. Build on them instead of redoing "
                "them (recall(<job id>) has the details):\n" + "\n".join(lines) + "\n\n")

    async def _on_finish(self, worker: WorkerAgent, outcome: str, summary: str, for_person: str = "") -> None:
        task = worker.task
        status = {
            "done": "done", "failed": "failed",
            "cancelled": "cancelled", "discarded": "cancelled",
        }.get(outcome, "done")

        self.store.update_task(task, status=status, result=summary, finished_ts=time.time())
        self.store.touch_session(worker.session, status="done", current_activity="")
        self._release_subscriptions(worker)
        thread = self.store.threads.get(task.thread_id or "")
        in_chat = is_session(thread, self.config)
        if not self._kept_for_a_follow_up(worker):
            self.store.drop_context(worker.session.id)  # finished: nothing to resume

        self.bus.emit(
            "worker.finished", session_id=worker.session.id, task_id=task.id,
            outcome=outcome, summary=summary,
        )

        if worker.speculative and outcome != "done":
            # Acting ahead did not pay off. Discard it quietly (s3.4).
            self.bus.emit(
                "speculation.discarded", session_id=worker.session.id,
                task_id=task.id, reason=summary[:200],
            )
            return

        if self.router and outcome in {"done", "failed"} and for_person and in_chat and not worker.speculative:
            # The clone tells the person itself: one AI call and its wait saved.
            # Rimuru hears of it with its next turn, without spending one on this.
            look = persona.card(worker.session)
            await self.router.channels.send(thread.channel, thread.key, for_person, clone=look["name"],
                                            worker_id=worker.session.id, color=look["color"], edge=look["edge"])
            self.router.loop.note(
                f'Task [{task.id}] "{task.title}" finished as {outcome}.\nworker: {worker.session.id}\n'
                f"thread: {thread.id}\nfiles: {worker.where}/\n{summary}\n"
                f"{look['name']} has already told the person: \"{for_person}\" - do not repeat it.")
            return

        if self.router and outcome in {"done", "failed"}:
            thread_id, channel = self._where(task)
            prefix = (
                "Speculative work you did not announce has succeeded. Decide "
                "whether to deliver it, and to whom.\n"
                if worker.speculative else ""
            )
            # The channel's own last step (a pull request, files sent) - said with the news.
            extra = ""
            finish = self.finishers.get(thread.channel) if thread is not None else None
            if finish is not None and outcome == "done":
                try:
                    extra = await finish(worker, thread, outcome, summary)
                except Exception as exc:  # noqa: BLE001 - the work is done either way
                    extra = f"could not finish up on {thread.channel}: {exc}"
            self.router.inject(
                f'{prefix}Task [{task.id}] "{task.title}" finished as {outcome}.\n'
                f"worker: {worker.session.id}\n"
                f"thread: {thread_id}\nchannel: {channel}\n"
                f"files: {worker.where}/\n"
                + (f"{extra}\n" if extra else "") + "\n"
                f"{summary}\n\n"
                f"{summary}\n\n"
                f"Tell whoever asked, in that channel's register.",
                # Whoever asked is waiting to hear (speculative work may stay quiet).
                waiting=None if worker.speculative else task.thread_id,
            )

    async def handback(self, worker: WorkerAgent, trigger_id: str, reason: str) -> bool:
        """A Worker returning an event that is not part of its task (s3.2)."""
        trigger = self.store.triggers.get(trigger_id)
        if trigger is None or self.router is None:
            return False
        trigger.routed_to = list(trigger.routed_to) + [self.router.session.id]
        self.store.put_trigger(trigger)
        self.handbacks += 1
        self.bus.emit(
            "worker.handback", session_id=worker.session.id,
            trigger_id=trigger_id, reason=reason,
        )
        rendered = inline_trigger(trigger, self.store, cap=self.config.inline_content_cap)
        self.router.inject(
            f"Handed back by worker [{worker.session.id}] ({reason}):\n\n{rendered}\n\n"
            f"It is not part of that worker's task. Route it afresh.",
            trigger_id=trigger_id,
        )
        return True

    def _release_subscriptions(self, worker: WorkerAgent) -> None:
        # A Worker that has stopped must not keep swallowing thread events.
        for thread_id in list(worker.session.subscriptions):
            self.store.unsubscribe(thread_id, worker.session.id)

    async def _on_session_finished(self, event: dict[str, Any]) -> None:
        worker = self.workers.get(event.get("session_id", ""))
        if worker is None:
            return
        if self.shutting_down:
            # Found while building exact resume: a normal shutdown used to mark
            # every running job "failed", so only a crash was ever resumed.
            return
        self._release_subscriptions(worker)
        task = worker.task
        if not self._kept_for_a_follow_up(worker):
            self.store.drop_context(worker.session.id)
        if task.status in {"queued", "running"}:
            # The loop ended without finish(): step cap, crash, or API failure.
            reason = worker.session.current_activity or worker.session.status
            self.store.update_task(
                task, status="failed", result=f"session ended without finishing: {reason}",
                finished_ts=time.time(),
            )
            self.bus.emit("worker.finished", session_id=worker.session.id,
                          task_id=task.id, outcome="failed", summary=reason)
        self._launch_next()

    # ------------------------------------------------------------------ #
    async def stop(self, task: Task, *, reason: str = "cancelled") -> None:
        worker = self.workers.get(task.worker_id or "")
        self.store.update_task(
            task, status="cancelled", result=f"cancelled: {reason}", finished_ts=time.time()
        )
        if worker:
            self._release_subscriptions(worker)
            self.store.drop_context(worker.session.id)
            if worker.session.id in self.launched:
                worker.loop.request_stop(status="stopped")
            else:
                # Still waiting in the backlog: it never ran, so just drop it.
                self.store.touch_session(worker.session, status="stopped")
        self.bus.emit("task.cancelled", task_id=task.id, reason=reason)
        self._launch_next()

    async def shutdown(self) -> None:
        self.shutting_down = True
        self.backlog.clear()
        for worker in list(self.workers.values()):
            if worker.session.id in self.launched:
                worker.loop.request_stop(status="stopped")
