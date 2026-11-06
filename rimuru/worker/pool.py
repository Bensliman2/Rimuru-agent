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

from ..context import inline_trigger
from ..ids import new_id
from ..llm import build_client
from ..models import TERMINAL, Session, Task
from ..sessions import claim_folder, is_session, name_session
from .session import WorkerAgent



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
    ) -> tuple[Session, bool]:
        """Create a Worker for a task. Returns (session, started_now)."""
        session = self.store.put_session(Session(
            id=new_id("worker"),
            kind="worker",
            title=task.title,
            model=self.config.model_for("worker"),
            status="queued",
            task_id=task.id,
        ))

        thread = self.store.threads.get(thread_id or task.thread_id or "")
        if task.folder:                  # resumed after a restart: the same folder as before
            root = Path(task.folder)
        else:
            if is_session(thread, self.config):
                name_session(self.store, thread, task.title, task.id)
            root = claim_folder(self.config, self.store, thread, task.title)
            self.store.update_task(task, folder=str(root))
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
            shell=self.shell,
            resume_note=resume_note,
            root=root,
            own_folder=bool(thread is not None and getattr(thread, "folder", "")
                            and Path(thread.folder) == root),
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

    async def _on_finish(self, worker: WorkerAgent, outcome: str, summary: str) -> None:
        task = worker.task
        status = {
            "done": "done", "failed": "failed",
            "cancelled": "cancelled", "discarded": "cancelled",
        }.get(outcome, "done")

        self.store.update_task(task, status=status, result=summary, finished_ts=time.time())
        self.store.touch_session(worker.session, status="done", current_activity="")
        self._release_subscriptions(worker)

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

        if self.router and outcome in {"done", "failed"}:
            thread_id, channel = self._where(task)
            prefix = (
                "Speculative work you did not announce has succeeded. Decide "
                "whether to deliver it, and to whom.\n"
                if worker.speculative else ""
            )
            self.router.inject(
                f'{prefix}Task [{task.id}] "{task.title}" finished as {outcome}.\n'
                f"worker: {worker.session.id}\n"
                f"thread: {thread_id}\nchannel: {channel}\n"
                f"files: {worker.where}/\n\n"
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
        self._release_subscriptions(worker)
        task = worker.task
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
            if worker.session.id in self.launched:
                worker.loop.request_stop(status="stopped")
            else:
                # Still waiting in the backlog: it never ran, so just drop it.
                self.store.touch_session(worker.session, status="stopped")
        self.bus.emit("task.cancelled", task_id=task.id, reason=reason)
        self._launch_next()

    async def shutdown(self) -> None:
        self.backlog.clear()
        for worker in list(self.workers.values()):
            if worker.session.id in self.launched:
                worker.loop.request_stop(status="stopped")
