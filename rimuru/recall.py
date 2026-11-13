"""Chain of Recall (article s3.3).

Every object in the system has an ID, and whenever one is injected the IDs of
its closely related objects come with it. Those IDs link the objects into a
reference graph. A recalled object is itself rendered with Inline & Spill, so
the result inlines the summaries and IDs of the *next* hop. Starting from the
event in front of it, the model can therefore walk outwards hop by hop, as far
as it actually needs, instead of the system guessing up front.

This is what keeps a single long-lived Router session viable: it does not need
every task's history resident in its window, because it can always find its way
back to any of it.
"""
from __future__ import annotations

from typing import Any

from .context import inline_object, inline_trigger
from .ids import kind_of


def recall(obj_id: str, store, *, full: bool = False, cap: int = 280) -> str:
    """Resolve one ID into text, with the next hop's IDs inlined."""
    obj_id = (obj_id or "").strip()
    kind = kind_of(obj_id)
    if kind is None:
        return f'recall: "{obj_id}" is not a known object ID.'

    if kind == "trigger":
        return _recall_trigger(obj_id, store, full=full, cap=cap)
    if kind == "thread":
        return _recall_thread(obj_id, store, full=full, cap=cap)
    if kind in {"worker", "router", "watchdog"}:
        return _recall_session(obj_id, store, full=full, cap=cap)
    if kind == "task":
        return _recall_task(obj_id, store, full=full, cap=cap)
    if kind == "job":
        return _recall_job(obj_id, store, full=full)
    if kind == "finding":
        return _recall_finding(obj_id, store)

    rendered = inline_object(obj_id, store, cap)
    return rendered or f'recall: nothing stored under "{obj_id}".'


def _recall_finding(obj_id: str, store) -> str:
    from . import notebook

    f = store.findings.get(obj_id)
    if f is None:
        return f'recall: nothing stored under "{obj_id}".'
    out = (f"finding {f.id} ({f.status}{', closed by ' + f.closed_by if f.closed_by else ''}) "
           f"in thread {f.scope}, task {f.task_id}, worker {f.worker_id}\n{notebook.line(store, f).strip()}")
    linked = [store.findings.get(f.other)] + [g for g in store.findings.values() if g.other == f.id]
    for g in linked:
        if g is not None:
            out += f"\n  related ({g.status}): {notebook.line(store, g).strip()}"
    return out


def _recall_trigger(obj_id: str, store, *, full: bool, cap: int) -> str:
    trigger = store.triggers.get(obj_id)
    if not trigger:
        return f'recall: no trigger "{obj_id}".'

    lines = [f"=== {obj_id} ==="]
    lines.append(f"channel: {trigger.channel} {trigger.thread_key}")
    lines.append(f"author: {trigger.author}   kind: {trigger.kind}")
    lines.append(f"verdict: {trigger.verdict} ({trigger.verdict_reason or 'n/a'})")
    if trigger.route:
        lines.append(f"routed: {trigger.route} -> {', '.join(trigger.routed_to) or 'nobody'}")
    lines.append("content:")
    lines.append(trigger.content if full else trigger.content[: cap * 4])

    # --- next hop ---
    hops: list[str] = []
    if trigger.thread_id:
        hops.append(f"thread {trigger.thread_id}")
    if trigger.reply_to:
        hops.append(f"parent message {trigger.reply_to}")
    if trigger.thread_id and (thread := store.threads.get(trigger.thread_id)):
        for worker_id in thread.subscribers:
            hops.append(f"subscribed worker {worker_id}")
        idx = thread.trigger_ids.index(obj_id) if obj_id in thread.trigger_ids else -1
        if idx > 0:
            hops.append(f"previous in thread {thread.trigger_ids[idx - 1]}")
        if 0 <= idx < len(thread.trigger_ids) - 1:
            hops.append(f"next in thread {thread.trigger_ids[idx + 1]}")
    if hops:
        lines.append("next hop: " + " | ".join(hops))
    return "\n".join(lines)


def _recall_thread(obj_id: str, store, *, full: bool, cap: int) -> str:
    thread = store.threads.get(obj_id)
    if not thread:
        return f'recall: no thread "{obj_id}".'

    lines = [f'=== {obj_id} "{thread.title}" ===']
    lines.append(f"channel: {thread.channel} {thread.key}")
    lines.append(f"triggers: {len(thread.trigger_ids)}")
    lines.append(f"subscribed workers: {', '.join(thread.subscribers) or 'none'}")

    ids = thread.trigger_ids if full else thread.trigger_ids[-12:]
    lines.append("")
    for trigger_id in ids:
        trigger = store.triggers.get(trigger_id)
        if trigger:
            lines.append(inline_trigger(trigger, store, cap=cap, depth=0))
    if not full and len(thread.trigger_ids) > len(ids):
        lines.append(
            f'... {len(thread.trigger_ids) - len(ids)} earlier spilled '
            f'-> recall("{obj_id}", full=true)'
        )

    related_tasks = [t.id for t in store.tasks.values() if t.thread_id == obj_id]
    if related_tasks:
        lines.append("next hop: tasks " + ", ".join(related_tasks))
    return "\n".join(lines)


def _recall_session(obj_id: str, store, *, full: bool, cap: int) -> str:
    session = store.sessions.get(obj_id)
    if not session:
        return f'recall: no session "{obj_id}".'

    lines = [f'=== {obj_id} ({session.kind}) "{session.title}" ===']
    lines.append(f"status: {session.status}   steps: {session.step_count}   model: {session.model}")
    if session.current_activity:
        lines.append(f"doing: {session.current_activity}")
    if session.task_id:
        lines.append(f"task: {session.task_id}")
    if session.subscriptions:
        lines.append(f"subscribed threads: {', '.join(session.subscriptions)}")

    steps = store.transcript(obj_id)
    shown = steps if full else steps[-6:]
    if shown:
        lines.append("")
        lines.append(f"--- last {len(shown)} of {len(steps)} steps ---")
        for step in shown:
            lines.append(f"[{step.index}] {(step.text or '').strip()[:cap]}")
            for call in step.tool_calls:
                args = ", ".join(f"{k}={str(v)[:50]}" for k, v in (call.get("args") or {}).items())
                lines.append(f"     -> {call.get('name')}({args})")
    if not full and len(steps) > len(shown):
        lines.append(f'... earlier steps spilled -> recall("{obj_id}", full=true)')
    return "\n".join(lines)


def _recall_task(obj_id: str, store, *, full: bool, cap: int) -> str:
    task = store.tasks.get(obj_id)
    if not task:
        return f'recall: no task "{obj_id}".'

    lines = [f'=== {obj_id} "{task.title}" ===']
    lines.append(f"status: {task.status}")
    duration = task.duration
    if duration is not None:
        lines.append(f"elapsed: {duration:.0f}s")
    lines.append("brief:")
    lines.append(task.brief if full else task.brief[: cap * 3])
    if task.result:
        lines.append("result:")
        lines.append(task.result if full else task.result[: cap * 3])

    hops = [h for h in (
        f"worker {task.worker_id}" if task.worker_id else "",
        f"thread {task.thread_id}" if task.thread_id else "",
        f"origin trigger {task.origin_trigger_id}" if task.origin_trigger_id else "",
    ) if h]
    if hops:
        lines.append("next hop: " + " | ".join(hops))
    return "\n".join(lines)


def _recall_job(obj_id: str, store, *, full: bool) -> str:
    job = store.jobs.get(obj_id)
    if not job:
        return f'recall: no job "{obj_id}".'
    lines = [f"=== {obj_id} ==="]
    lines.append(f"command: {job.command}")
    lines.append(f"status: {job.status}")
    lines.append("output:")
    lines.append(job.result if full else job.result[:1200])
    lines.append(f"next hop: session {job.session_id}")
    return "\n".join(lines)


def spill_digest(store, limit: int = 12) -> str:
    """A compact view of what passed straight through to Workers.

    The Router never had these in its window (they went to subscribers on the
    Fast Path), but it can still see that they happened and recall any of them.
    """
    ids = store.router_spill[-limit:]
    if not ids:
        return "Nothing has passed through to workers yet."
    lines = [f"{len(store.router_spill)} events bypassed you via subscriptions. Most recent:"]
    for trigger_id in ids:
        trigger = store.triggers.get(trigger_id)
        if not trigger:
            continue
        preview = (trigger.content or "").replace("\n", " ")[:80]
        lines.append(
            f'  [{trigger_id}] {trigger.thread_key} {trigger.author}: "{preview}" '
            f'-> {", ".join(trigger.routed_to)}'
        )
    return "\n".join(lines)


def describe_graph(store) -> dict[str, Any]:
    """Reference graph for the UI's recall explorer."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for thread in store.threads.values():
        nodes.append({"id": thread.id, "kind": "thread", "label": thread.title})
        for worker_id in thread.subscribers:
            edges.append({"from": thread.id, "to": worker_id, "kind": "subscribes"})
    for session in store.sessions.values():
        nodes.append({"id": session.id, "kind": session.kind, "label": session.title})
        if session.task_id:
            edges.append({"from": session.id, "to": session.task_id, "kind": "executes"})
    for task in store.tasks.values():
        nodes.append({"id": task.id, "kind": "task", "label": task.title})
        if task.thread_id:
            edges.append({"from": task.id, "to": task.thread_id, "kind": "about"})
    return {"nodes": nodes, "edges": edges}
