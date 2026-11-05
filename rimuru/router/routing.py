"""Event routing: subscription and pass-through (article s3.2).

The Router layer keeps interaction continuous across channels, but if every
follow-up event for an already-assigned task had to go through that one global
node, it would carry a heavy load and slow everything down. So the Fast/Slow
Path split applies to routing as well:

    Subscribed (Fast Path)    code hands follow-up events in a Thread straight
                              to the Worker(s) subscribed to it. The Router does
                              not spend a turn, let alone a tool call, forwarding.

    Unsubscribed (Slow Path)  the Router evaluates and dispatches events that
                              have no owner yet - cold starts and cross-task
                              coordination only.

Events that pass through are still *visible* to the Router: their IDs go to the
spill area, so it keeps global awareness and can recall any of them, without
paying for them in its context window.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..context import inline_trigger
from ..ids import ROUTER_SESSION_ID
from ..models import R_PASSTHROUGH, R_ROUTER, V_ACCEPTED, V_CANDIDATE, V_DROPPED_FAST, Trigger

InjectRouter = Callable[[str, str | None], None]
InjectWorker = Callable[[str, str, str | None], bool]


class EventRouter:
    def __init__(
        self,
        *,
        store: Any,
        bus: Any,
        config: Any,
        event_filter: Any,
        inject_router: InjectRouter,
        inject_worker: InjectWorker,
    ) -> None:
        self.store = store
        self.bus = bus
        self.config = config
        self.filter = event_filter
        self._inject_router = inject_router
        self._inject_worker = inject_worker
        self.counters = {"passthrough": 0, "to_router": 0, "dropped": 0}

    # ------------------------------------------------------------------ #
    async def ingest(self, trigger: Trigger) -> str:
        """Take one environmental event from arrival to the session that owns it."""
        # 1. Place it in its Thread. Consecutive events on the same PR / IM topic
        #    / email chain form one stream (article s3.2).
        thread = self.store.get_or_create_thread(
            trigger.channel, trigger.thread_key,
            trigger.meta.get("thread_title") or trigger.thread_key,
        )
        self.store.attach_trigger_to_thread(thread, trigger)
        self.store.put_trigger(trigger)
        self.bus.emit("trigger.received", trigger=trigger.to_dict())

        # 2. Fast Path filtering: drop the bulk of the noise with no model call.
        verdict, reason = self.filter.evaluate_fast(trigger)
        if verdict == V_DROPPED_FAST:
            self.counters["dropped"] += 1
            return verdict

        # 3. Already owned? Hand it straight to the subscriber(s). The Worker has
        #    the task context and judges relevance itself, so neither the semantic
        #    filter nor the Router needs to be involved.
        subscribers = self.store.subscribers_of(thread.id)
        if subscribers:
            delivered: list[str] = []
            rendered = inline_trigger(trigger, self.store, cap=self.config.inline_content_cap)
            text = (
                f"New event on a thread you subscribe to "
                f'({thread.channel} {thread.key} "{thread.title}"):\n\n{rendered}\n\n'
                f"If this is part of your task, act on it. If it is not, call "
                f"unsubscribe or handback so it can be routed elsewhere."
            )
            for worker_id in subscribers:
                if self._inject_worker(worker_id, text, trigger.id):
                    delivered.append(worker_id)

            if delivered:
                trigger.verdict = V_ACCEPTED
                trigger.verdict_reason = "passed through to subscribed worker(s)"
                trigger.route = R_PASSTHROUGH
                trigger.routed_to = delivered
                self.store.put_trigger(trigger)
                # Visible to the Router without entering its window.
                self.store.router_spill.append(trigger.id)
                self.counters["passthrough"] += 1
                self.bus.emit(
                    "trigger.routed", trigger_id=trigger.id, route=R_PASSTHROUGH,
                    to=delivered, thread_id=thread.id,
                )
                return V_ACCEPTED

        # 4. No owner: judge it, then let the Router decide. A person writing to
        #    Rimuru directly is never noise - skipping the model call here saves
        #    seconds on every chat message (the evaluation measured ~5s).
        direct = (trigger.kind == "human_direct"
                  or trigger.channel in self.config.conversation_channels)
        if direct:
            verdict, reason = V_ACCEPTED, "a person talking to Rimuru directly"
            trigger.verdict, trigger.verdict_reason = verdict, reason
            self.store.put_trigger(trigger)
            self.counters["direct"] = self.counters.get("direct", 0) + 1
        else:
            verdict, reason = await self.filter.evaluate_semantic(trigger)
        if verdict != V_ACCEPTED:
            self.counters["dropped"] += 1
            return verdict

        # Never trim what a person typed: a spilled message cost the Router a
        # recall round trip before it could even start.
        cap = 8000 if direct else self.config.inline_content_cap
        rendered = inline_trigger(trigger, self.store, cap=cap)
        text = (
            f"Unassigned event admitted by the filter ({reason}):\n\n{rendered}\n\n"
            f"Decide what to do with it. Dispatch work, reply, or ignore it."
        )
        self._inject_router(text, trigger.id)

        trigger.route = R_ROUTER
        trigger.routed_to = [ROUTER_SESSION_ID]
        self.store.put_trigger(trigger)
        self.counters["to_router"] += 1
        self.bus.emit(
            "trigger.routed", trigger_id=trigger.id, route=R_ROUTER,
            to=[ROUTER_SESSION_ID], thread_id=thread.id, reason=reason,
        )
        return V_ACCEPTED

    def stats(self) -> dict[str, Any]:
        return dict(self.counters)
