"""The Router layer (article s2.1).

One long-lived session that receives human interaction from every entry point.
All cross-task information and decisions pass through it, so it is the system's
global sequence point, coordination point and memory.

The single most important property is what it does NOT do: it never executes
tasks. It has no shell, no file tools, no way to touch a repository. That is a
deliberate structural constraint rather than a prompt-engineering hope - the
ablation in s7.1 shows a single session handed a subagent tool will often just
do the work itself, which is exactly what destroys throughput. Here the Router
cannot, because the tools do not exist.

Being interaction-only also keeps it cheap: long input, short output, which is
what makes batching pay off.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from typing import Any

from ..agent.loop import AgentLoop
from ..agent.tools import ToolRegistry, enum, obj, register_recall, s
from ..context import inline_task, inline_thread
from ..ids import ROUTER_SESSION_ID, new_id
from .. import memory
from .. import skills as skillbook
from ..models import Memory, Session, Task
from ..recall import spill_digest
from ..i18n import language_of, say, word
from ..text import fold

# People judge an assistant by how fast they get something that works
# (docs/speed.md: the same calculator took 14.6 minutes the old way, 1.9 now).
QUICK_NOTE = """
SPEED
  People judge you by how fast they get something that works. When you
  dispatch, ask for the simplest version that does what the person asked -
  extras can come later. When the work is done, say what they have and how to
  use it, and offer the improvements the clone left out as one short question.
"""

# A newer message that changes course, rather than adding to it. Only these make
# a reply composed before them unsafe to send. Matched against fold(message): no
# accents or vowel marks, أ is ا, ة is ه.
CORRECTION = re.compile(
    r"\b(actually|cancel|instead|never ?mind|stop|don'?t|do not|wait|scratch that|"
    r"forget|rather|no longer|hold on|undo|change of plan|"
    r"annule[rz]?|arrete[rz]?|attends|attendez|laisse[z]?\s+tomber|en\s+fait|plutot|finalement|"
    r"oublie[rz]?|au\s+lieu|ne\s+fais\s+pas|ne\s+faites\s+pas|changement\s+de\s+plan|"
    "توقف|اوقف|وقف|الغ|الغي|الغاء|انتظر|استني|بدلا|بدل|في\\s+الواقع|انس|انسي|تراجع|بلاش|"
    "لا\\s+تفعل|لا\\s+تعمل|لا\\s+تكمل|خلاص)\\b", re.IGNORECASE)

SYSTEM_PROMPT = """You are the Router layer of a Super-agent called Rimuru.

You are one continuous entity. Users reach you from several places - group chat,
email, code review comments, CI - and to them it is always the same agent. They
must never have to wonder which session holds which fact. You are the system's
global memory and its only voice.

WHAT YOU DO
  - Receive every environmental event that has no worker on it already.
  - Decide: dispatch work, answer, or stay quiet.
  - Reply to humans. You are the only part of the system that talks to them.
  - Keep track of how tasks relate to each other, and relay the rare piece of
    information that genuinely crosses between them.

WHAT YOU NEVER DO
  - You never carry out the work yourself. You have no shell and no file tools,
    on purpose. If something needs doing, dispatch it to a worker.
  - You never break a task into subtasks. Hand over the whole task; the worker
    plans it. You track tasks, not steps.
  - You never relay a worker's reasoning or tool output to a human. Workers
    report conclusions to you; you decide what is worth saying.

BATCHES
  Several events often arrive together because they queued while you were
  thinking. Handle all of them in the one turn, each with its own tool call.
  This is cheaper than taking them one at a time, so do not complain about it.

STAYING QUIET
  Most events need nothing. Silence is a correct and frequent outcome. Interrupt
  a person only when you need a decision or a sign-off from them.

CHANNEL MANNERS
  - Chat (im/discord/telegram/slack): at most 100 words. Short conclusions and
    direct questions only. Behave like a colleague who respects attention.
  - GitHub, email, documents: structured and information-dense. Give the
    background, what changed, and how it was verified.
  - Real GitHub (channel "gh", threads like owner/repo#12): only the people the
    owner allowed reach you there. For a code change, dispatch it on that
    thread: the clone works in a copy of the repository, and when it is done a
    draft pull request is opened by itself - link it ("pull request:") in your
    reply. Nothing is ever merged by you.

THE DIRECT CHAT (channel "console" - and "telegram", the same person on their phone)
  This is a conversation with one person, who may have no technical background.
  - Only the reply tool reaches the person. Text you write outside a tool call
    is never shown to them, so every answer - even a one-line one - goes
    through reply.
  - Plain, warm, short. No jargon, and never show internal IDs (trg_, thr_, wrk_,
    tsk_, job_) - those are for you, not them.
  - Answer in the language of the person's latest message, even though
    everything else you read is in English: each message says which. The title
    you give dispatch_task is shown to them too: write it in that language.
  - When you take something on, say in one line what you will do. When work
    finishes, say what they now have and how to use it. Mention a file name only
    when it helps them - with the folder from the result ("files:"), so they can
    find it on their computer.
  - Workers are NOT subscribed to this chat - you own it. If the person adds
    detail about something already in progress, pass it on with message_worker
    (the worker is shown next to each task). If they ask how it is going, answer
    from the task state you can see.
  - The person may keep several sessions: separate conversations, each about
    its own work, each its own thread (thr_..., shown with every message).
    Reply on the thread the message came from - pass that thread_id ("console"
    alone means the session that wrote last) - and pass it to dispatch_task too,
    so the clone is listed in that session. Keep sessions apart: do not bring
    one session's work into another unless the person does.
  - Each clone has a name (it comes with the dispatch result). Telling the
    person who is on it is fine - "Mika is on it" - and keeps it personal.
  - When a job from this chat finishes, its clone usually tells the person itself,
    and you get a note saying so: do not repeat it. A job you dispatch soon after
    goes to that same clone (it remembers the work) unless you pass fresh.
  - On Telegram (their phone): never paste code or file contents into a reply.
    Just do the job, then say in plain words what was done and how to use it.
    Only when they ask for the files or the code, in whatever language, call
    send_files - or, for a few lines they asked to see, reply with include_code.
  - Answer first. When the person asks for something, reply in the SAME turn
    you act on it (reply and dispatch_task can go together), so they never
    wait through several of your turns before hearing from you.

CONTEXT YOU NO LONGER HOLD
  Every object has an ID: trg_ (event), thr_ (thread), wrk_ (worker), tsk_ (task).
  Whatever is injected comes with the IDs of its neighbours. If a person refers
  to something you cannot see, call recall() on the nearest ID and follow the
  references outwards until you find it. Do not guess, and do not claim you have
  lost context - look it up.

  Events on threads a worker already owns go straight to that worker and never
  enter your window. Call spill() to see what has been happening without you.

LONG-TERM MEMORY
  You remember across sessions and restarts. When you learn something lasting,
  call remember() - one fact per call, in plain words:
    about="you"      the person: their name, what they like, how they want things done
    about="session"  this session's work: names, where things are, decisions made
    about="general"  anything else worth knowing later
  Do not remember passing chatter, things true only today, or anything secret
  (passwords, keys) - secrets are refused. What you remember comes back to you
  with the turns where it may matter: use it, without being asked. If the person
  says a memory is wrong, forget() it and remember the right thing. They can see
  every memory you make, and change or delete any of them.
"""


class RouterAgent:
    def __init__(
        self,
        *,
        store: Any,
        bus: Any,
        config: Any,
        client: Any,
        pool: Any,
        channels: Any,
    ) -> None:
        self.store = store
        self.bus = bus
        self.config = config
        self.pool = pool
        self.channels = channels

        self.session = store.sessions.get(ROUTER_SESSION_ID) or store.put_session(
            Session(
                id=ROUTER_SESSION_ID,
                kind="router",
                title="Router (global interaction and dispatch)",
                model=config.model_for("router"),
                status="idle",
            )
        )

        self.registry = ToolRegistry()
        self._register_tools()
        # The chat session a person wrote in last (see _tool_reply, _tool_dispatch).
        self.latest_console_thread: str | None = None
        self.latest_console_ts = 0.0

        self.loop = AgentLoop(
            session=self.session,
            client=client,
            store=store,
            bus=bus,
            config=config,
            system_prompt=SYSTEM_PROMPT + QUICK_NOTE,
            registry=self.registry,
            role="router",
            budget_chars=config.router_context_budget,
            max_steps=0,            # the Router is long-lived and never capped
            # Room to reason over a merged batch and still call the tools.
            max_tokens=4096,
        )
        # A person's message cuts in on a turn that something else started.
        self.loop.cut_in = lambda injection: injection.kind == "human"
        # An answer written but never sent still reaches the person (_after_turn).
        self.loop.on_turn_end = self._after_turn
        self._replied_ts = 0.0
        # What it remembers that may matter comes with each new turn - each
        # memory once per stretch of context (_memories_for).
        self.loop.annotate = self._memories_for
        self._shown: set[str] = set()
        self._shown_spill = 0
        # A turn that did its job ends there, with at most eight rounds of tools;
        # an AI service that fails is said in the chat, never left as silence.
        self.loop.ends_turn = self._ends_turn
        self.loop.max_rounds = 8
        self.loop.on_call_failed = self._call_failed
        self.loop.on_struggling = self._service_struggling
        self._told_ts = 0.0

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self.loop.start()
        self.store.touch_session(self.session, status="idle")

    def inject(self, text: str, trigger_id: str | None = None, **meta: Any) -> None:
        # meta "waiting": the chat thread a person is waiting on for this news.
        self.loop.inject(text, kind="event", trigger_id=trigger_id, **meta)

    # ------------------------------------------------------------------ #
    # a turn that did its job, and a service that fails
    # ------------------------------------------------------------------ #
    # What each tool says when it did what was asked. When every call in a step
    # is one of these, the turn is over: no call just to read "sent".
    DONE = {
        "reply": ("Sent on ",), "ignore": ("Noted, no action",), "dispatch_task": ("Dispatched ",),
        "message_worker": ("Delivered to ",), "send_files": ("Sent on Telegram",),
        "remember": ("Remembered [", "Already remembered ["), "forget": ("Forgot [",),
        "stop_task": ("Stopped ",),
    }

    def _ends_turn(self, outputs: list[tuple[str, str]]) -> bool:
        for name, output in outputs:
            if not output.startswith(self.DONE.get(name, ("\0",))):
                return False
            if name == "reply" and "include_code" in output:
                return False                 # it may send the code after all
        # Whoever wrote in this turn has had an answer - or nobody is waiting.
        return (self._replied_ts >= self.loop.turn_opened_ts
                or not self._waiting_threads(self.loop.turn_events))

    def _service_struggling(self, why: str) -> None:
        """The client is retrying. Retries in the first seconds are routine; past
        that, whoever is waiting hears why, instead of silence."""
        if time.time() - self.loop.turn_started_ts < 8:
            return
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._tell_waiting(again=True, why=_why(why)))

    async def _call_failed(self, exc: Exception, again: bool) -> None:
        await self._tell_waiting(again=again, why=_why(exc))

    async def _tell_waiting(self, *, again: bool, why: str) -> None:
        opened = self.loop.turn_opened_ts
        if self._replied_ts >= opened or (again and self._told_ts >= opened):
            return
        waiting = self._waiting_threads(self.loop.turn_events)
        if not waiting:
            return
        self._told_ts = time.time()
        for thread_id in waiting:
            thread = self.store.threads.get(thread_id)
            if thread is None:
                continue
            # In the language the person wrote in (rimuru/i18n.py).
            said = next((m["text"] for m in reversed(self.store.chat_of(thread.key)) if m["role"] == "user"), "")
            lang = language_of(said)
            text = say("service_down" if again else "service_refused", lang, why=word(why, lang))
            await self.channels.send(thread.channel, thread.key, text)
        self.bus.emit("router.told_failure", again=again, why=why)

    # ------------------------------------------------------------------ #
    # an answer that was written but never sent
    # ------------------------------------------------------------------ #
    async def _after_turn(self, text: str) -> None:
        """Send the turn's last text when the model forgot to call reply.

        Found in use with a free model: it answered a person's question in plain
        text, and announced a finished job the same way - so the person saw
        nothing at all. Only when the turn sent no reply anywhere, and exactly
        one chat session is waiting on it (a person's message, or the end of
        their job); otherwise there is no telling where the text belongs.
        """
        text = (text or "").strip()
        if not text or self._replied_ts >= self.loop.turn_opened_ts:
            return
        waiting = self._waiting_threads(self.loop.turn_events)
        if len(waiting) != 1:
            return
        thread = self.store.threads.get(waiting.pop())
        if thread is None:
            return
        sent, _note = await self.channels.send(thread.channel, thread.key, text)
        self._replied_ts = time.time()
        self.bus.emit("agent.replied", thread_id=thread.id, channel=thread.channel,
                      thread_key=thread.key, text=sent)
        self.bus.emit("router.unsent_answer", thread_id=thread.id)

    def _waiting_threads(self, events: list[Any]) -> set[str]:
        conv = self.config.conversation_channels
        out: set[str] = set()
        for injection in events:
            thread_id = None
            if injection.kind == "human":
                trigger = self.store.triggers.get(injection.trigger_id or "")
                # Not when Rimuru chose to stay quiet on it (ignore).
                if trigger is not None and not (trigger.verdict_reason or "").startswith("router:"):
                    thread_id = trigger.thread_id
            else:
                thread_id = injection.meta.get("waiting")
            thread = self.store.threads.get(thread_id or "")
            if thread is not None and thread.channel in conv:
                out.add(thread.id)
        return out

    # ------------------------------------------------------------------ #
    # tools
    # ------------------------------------------------------------------ #
    def _register_tools(self) -> None:
        reg = self.registry
        register_recall(reg)

        reg.add(
            "dispatch_task",
            "Hand a whole task to a new worker with its own isolated context and "
            "full execution tools. The worker is automatically subscribed to the "
            "thread the request came from, so follow-ups reach it directly without "
            "going through you. Use this for anything that needs actual work.",
            obj(
                title=s("Short imperative title, e.g. 'fix CSV exporter crash on empty input'"),
                brief=s("Everything the worker needs: who asked, where, what they said, "
                        "what done looks like. The worker cannot see your context."),
                thread_id=s("Thread ID the request came from (thr_...), if any", optional=True),
                speculative=s("'true' to work in the background without telling anyone, "
                              "and surface a result only if it succeeds", optional=True),
                fresh=s("'true' for a new clone even though one just finished in this session "
                        "(by default that clone carries on: it remembers the work). Only when "
                        "the new job has nothing to do with what it did.", optional=True),
            ),
            self._tool_dispatch,
        )

        reg.add(
            "reply",
            "Say something to the humans on a thread. This is the only way anything "
            "reaches a person. Respect the channel's manners - chat replies are "
            "capped at 100 words and the cap is enforced.",
            obj(
                thread_id=s("Thread to reply on (thr_...)"),
                text=s("What to say. Conclusions and direct questions only."),
                include_code=s("'true' only when the person asked to see code, in any language. Otherwise "
                               "code blocks are left out on channels read on a phone.", optional=True),
            ),
            self._tool_reply,
        )

        reg.add(
            "ignore",
            "Record that an event needs no action. Use this freely - most events "
            "need nothing, and saying so explicitly is how you stay quiet.",
            obj(
                trigger_id=s("Event being dismissed (trg_...)", optional=True),
                reason=s("One short phrase"),
            ),
            self._tool_ignore,
        )

        reg.add(
            "list_tasks",
            "List current and recent tasks with their status, worker and thread.",
            obj(status=s("Filter by status: queued|running|done|failed|cancelled", optional=True)),
            self._tool_list_tasks,
        )

        reg.add(
            "stop_task",
            "Stop a task that is no longer wanted, for instance when the requester "
            "withdraws it. Identify it by task ID, or by the thread it belongs to.",
            obj(
                task_id=s("Task ID (tsk_...)", optional=True),
                thread_id=s("Or: the thread whose live task should stop (thr_...)",
                            optional=True),
                reason=s("Why"),
            ),
            self._tool_stop_task,
        )

        reg.add(
            "message_worker",
            "Relay a piece of information to a running worker. Only for things that "
            "genuinely cross between tasks - workers that share a thread already see "
            "each other's events without your help.",
            obj(worker_id=s("Worker session (wrk_...)"), text=s("What it needs to know")),
            self._tool_message_worker,
        )

        reg.add(
            "send_files",
            "Telegram only: send the person the files a finished job made, as attachments. "
            "Only when they ask for the files or the code - never on your own.",
            obj(thread_id=s("The Telegram thread (thr_...)"),
                task_id=s("The job (tsk_...); default: the latest finished one there", optional=True)),
            self._tool_send_files,
        )

        reg.add(
            "spill",
            "Show the events that bypassed you by passing straight through to "
            "subscribed workers. Use it to catch up on what happened without you.",
            obj(),
            self._tool_spill,
        )

        reg.add(
            "remember",
            "Keep a lasting fact in long-term memory, one per call: about the person, "
            "about this session's work, or general. The person sees it being kept.",
            obj(text=s("The fact, stated plainly"),
                about=enum("Who or what it is about", ["you", "session", "general"]),
                thread_id=s("For about=session: the session's thread (thr_...)", optional=True)),
            self._tool_remember,
        )
        reg.add(
            "forget",
            "Delete a memory that is wrong or no longer true (its mem_ ID).",
            obj(memory_id=s("The memory's ID, mem_...")),
            self._tool_forget,
        )

        reg.add(
            "thread_context",
            "Read a thread's recent messages. Cheaper than recall when you just "
            "need to see what was being discussed.",
            obj(thread_id=s("Thread ID (thr_...)")),
            self._tool_thread_context,
        )

    # ------------------------------------------------------------------ #
    async def _tool_dispatch(self, args: dict[str, Any], loop: AgentLoop) -> str:
        title = str(args.get("title") or "untitled task").strip()
        brief = str(args.get("brief") or "").strip()
        thread_id = str(args.get("thread_id") or "").strip() or None
        speculative = str(args.get("speculative") or "").lower() in {"true", "yes", "1"}
        fresh = str(args.get("fresh") or "").lower() in {"true", "yes", "1"}

        if thread_id and thread_id not in self.store.threads:
            thread_id = None
        if thread_id is None:
            # Found in real use: work asked for in the chat was dispatched without
            # its thread, so the clone belonged to no session. Link it to the
            # chat session this turn is about, when that is clear.
            thread_id = self._session_of_this_turn(loop)

        # The message this job answers: one from this turn on that thread that no
        # job answers yet - not merely the thread's latest, which may be a newer
        # message that arrived while Rimuru was thinking.
        origin = None
        answered = {t.origin_trigger_id for t in self.store.tasks.values() if t.origin_trigger_id}
        for injection in getattr(loop, "turn_events", None) or []:
            trigger = self.store.triggers.get(getattr(injection, "trigger_id", None) or "")
            if trigger is not None and trigger.thread_id == thread_id and trigger.id not in answered:
                origin = trigger.id
                break
        if origin is None and thread_id and (thread := self.store.threads.get(thread_id)) and thread.trigger_ids:
            origin = thread.trigger_ids[-1]

        # Skills the person forced for the message this answers (/skill_<name>):
        # the job gets them in full, whether or not the brief mentions them.
        forced = self._forced_skills(loop, thread_id)
        task = self.store.put_task(Task(
            id=new_id("task"),
            title=title,
            brief=brief,
            thread_id=thread_id,
            origin_trigger_id=origin,
            skills=forced,
        ))
        try:
            # A job on the heels of the last one in this session goes to the clone
            # that just finished it: it remembers the work and need not rediscover it.
            warm = None if speculative or fresh else self.pool.warm_clone(thread_id)
            worker, started = await self.pool.submit(
                task, thread_id=thread_id, speculative=speculative, continue_from=warm
            )
        except Exception as exc:  # noqa: BLE001 - e.g. the repository could not be copied
            self.store.update_task(task, status="failed", finished_ts=time.time(),
                                   result=f"could not start: {exc}")
            return f"Could not start {task.id}: {exc}. Tell whoever asked, plainly."

        mode = "speculatively (nothing is said unless it succeeds)" if speculative else "normally"
        sub = (f" and subscribed to {thread_id}"
               if thread_id and not speculative else "")
        thread = self.store.threads.get(thread_id or "")
        if thread is not None and thread.channel in self.config.conversation_channels:
            sub = (" It is NOT subscribed to the chat - relay follow-ups to it with "
                   "message_worker.")
        when = ("It is running now." if started else
                f"All {self.config.max_workers} worker slots are busy, so it is queued "
                f"and starts when one frees up.")
        again = " It is the same clone that just finished here, and it remembers that work." if warm else ""
        return (
            f'Dispatched {mode}: {task.id} "{title}" -> worker {worker.id}, a clone named '
            f"{worker.name or 'without a name'}{sub}.{again} {when}\n"
            f"Follow-up events on that thread now reach the worker directly. "
            f"It will report back to you when it has a conclusion."
        )

    def _session_of_this_turn(self, loop: AgentLoop) -> str | None:
        conv = self.config.conversation_channels
        for msg in reversed([m for m in loop.messages[-4:] if m.role == "user"]):
            ids = {t for t in re.findall(r"thr_[0-9a-f]+", msg.as_text())
                   if (th := self.store.threads.get(t)) is not None and th.channel in conv}
            if ids:
                return ids.pop() if len(ids) == 1 else None      # two sessions: do not guess
        if self.latest_console_thread and time.time() - self.latest_console_ts < 120:
            return self.latest_console_thread
        return None

    async def _tool_reply(self, args: dict[str, Any], loop: AgentLoop) -> str:
        thread_id = str(args.get("thread_id") or "").strip()
        text = str(args.get("text") or "").strip()
        if not text:
            return "Nothing to send."

        thread = self.store.threads.get(thread_id)
        if thread is None and thread_id.startswith("console"):
            thread = (self.store.threads.get(self.latest_console_thread or "")
                      or self.store.get_or_create_thread("console", "direct", "direct line"))
        if thread is None:
            # Never guess. Posting into whichever thread happened to be most
            # recent would put a reply in front of the wrong people.
            return (
                f'No thread "{thread_id}". Pass a thread ID (thr_...), or "console" '
                f"for the maintainers' direct line. Nothing was sent."
            )

        # Fresh-reply check. Found in the evaluation: the person said "cancel the
        # manual" while a reply about the manual was being composed, and the reply
        # went out anyway. If they wrote again since this turn began AND what they
        # wrote changes course, hold the reply. A new request on top ("also add
        # coupons") leaves the reply true, so it goes out - holding those too cost
        # 25 extra seconds before the first answer in the re-run.
        newer_note = ""
        if thread.channel in self.config.conversation_channels and loop.turn_started_ts:
            newer = [
                t for t in (self.store.triggers.get(i) for i in thread.trigger_ids)
                if t is not None and t.kind == "human_direct" and t.ts > loop.turn_started_ts
            ]
            corrections = [t for t in newer if CORRECTION.search(fold(t.content))]
            if corrections:
                self.bus.emit("router.reply_held", thread_id=thread.id, newer=len(newer))
                latest = " ".join(corrections[-1].content.split())[:300]
                return (f'Not sent: the person wrote again while you were thinking: "{latest}". '
                        f"Their message comes in with this result - answer with it in mind.")
            if newer:
                newer_note = ("\nThe person has written again since this turn began; their "
                              "new message comes in with this result.")

        sent, note = await self.channels.send(
            thread.channel, thread.key, text,
            include_code=str(args.get("include_code") or "").lower() in {"true", "yes", "1"})
        self._replied_ts = time.time()
        self.bus.emit(
            "agent.replied", thread_id=thread.id, channel=thread.channel,
            thread_key=thread.key, text=sent,
        )
        return (f"Sent on {thread.channel} {thread.key}: {sent}" + (f"\n({note})" if note else "")
                + newer_note)

    async def _tool_ignore(self, args: dict[str, Any], loop: AgentLoop) -> str:
        trigger_id = str(args.get("trigger_id") or "").strip()
        reason = str(args.get("reason") or "no action needed").strip()
        if trigger := self.store.triggers.get(trigger_id):
            trigger.verdict_reason = f"router: {reason}"
            self.store.put_trigger(trigger)
        self.bus.emit("router.ignored", trigger_id=trigger_id or None, reason=reason)
        return f"Noted, no action: {reason}"

    async def _tool_list_tasks(self, args: dict[str, Any], loop: AgentLoop) -> str:
        wanted = str(args.get("status") or "").strip().lower()
        tasks = self.store.all_tasks()
        if wanted:
            tasks = [t for t in tasks if t.status == wanted]
        if not tasks:
            return "No tasks."
        return "\n".join(
            inline_task(t, self.store, cap=self.config.inline_content_cap) for t in tasks[:25]
        )

    async def _tool_stop_task(self, args: dict[str, Any], loop: AgentLoop) -> str:
        task_id = str(args.get("task_id") or "").strip()
        thread_id = str(args.get("thread_id") or "").strip()
        reason = str(args.get("reason") or "no longer needed").strip()

        task = self.store.tasks.get(task_id) if task_id else None
        if task is None and thread_id:
            live = [
                t for t in self.store.all_tasks()
                if t.thread_id == thread_id and t.status in {"running", "queued"}
            ]
            task = live[0] if live else None
            if task is None:
                return f"Nothing is running on {thread_id}; there is nothing to stop."
        if task is None:
            return f'No task "{task_id}". Call list_tasks to see what is live.'
        if task.status not in {"running", "queued"}:
            return f"{task.id} already {task.status}; nothing to stop."

        await self.pool.stop(task, reason=reason)
        return f'Stopped {task.id} "{task.title}": {reason}'

    async def _tool_message_worker(self, args: dict[str, Any], loop: AgentLoop) -> str:
        worker_id = str(args.get("worker_id") or "").strip()
        text = str(args.get("text") or "").strip()
        worker = self.pool.workers.get(worker_id)
        forced = self._forced_skills(loop, worker.task.thread_id if worker is not None else None)
        if forced:
            # The person forced a skill for what is being passed on: it goes along, in full.
            text += "\n\n" + skillbook.for_forced(skillbook.resolve(self.config.skills_dir, forced)[0])
        ok = self.pool.inject(worker_id, f"Relayed by the Router:\n{text}", kind="human")
        return f"Delivered to {worker_id}." if ok else f"{worker_id} is not a live worker."

    async def _tool_send_files(self, args: dict[str, Any], loop: AgentLoop) -> str:
        thread = self.store.threads.get(str(args.get("thread_id") or "").strip())
        if thread is None or thread.channel != "telegram":
            return "send_files is for Telegram chats only; in the app the person has the folder."
        telegram = self.channels.channels.get("telegram")
        if telegram is None or not hasattr(telegram, "send_job_files"):
            return "Telegram is not connected right now."
        wanted = str(args.get("task_id") or "").strip()
        done = sorted((t for t in self.store.tasks.values()
                       if t.thread_id == thread.id and t.status == "done" and (not wanted or t.id == wanted)),
                      key=lambda t: t.finished_ts or 0)
        if not done:
            return "No finished job on that chat has files to send."
        sent = await telegram.send_job_files(done[-1])
        return (f"Sent on Telegram: {', '.join(sent)}." if sent else
                "There were no files to send - none made, or too big for Telegram (10 MB).")

    def _forced_skills(self, loop: AgentLoop, thread_id: str | None) -> list[str]:
        """Skills the person forced (/skill_<name>) for this turn's messages on a thread."""
        names: list[str] = []
        for injection in getattr(loop, "turn_events", None) or []:
            trigger = self.store.triggers.get(getattr(injection, "trigger_id", None) or "")
            if trigger is not None and (thread_id is None or trigger.thread_id == thread_id):
                names += [n for n in trigger.meta.get("skills") or [] if n not in names]
        return names

    async def _tool_spill(self, args: dict[str, Any], loop: AgentLoop) -> str:
        return spill_digest(self.store)

    # ------------------------------------------------------------------ #
    # long-term memory (rimuru/memory.py)
    # ------------------------------------------------------------------ #
    def _memories_for(self, batch: list[Any]) -> str:
        if self.loop.spilled_turns != self._shown_spill:
            # Older turns left the window, and the memories shown in them with it.
            self._shown.clear()
            self._shown_spill = self.loop.spilled_turns
        text = "\n".join(i.text for i in batch)
        conv = self.config.conversation_channels
        keys = {th.key for t in re.findall(r"thr_[0-9a-f]+", text)
                if (th := self.store.threads.get(t)) is not None and th.channel in conv}
        picked = memory.relevant(self.store.memories.values(), text=text, session_keys=keys,
                                 exclude=self._shown)
        # The person's skills (rimuru/skills.py) come up only when they talk about
        # skills - "use my deploy skill", "what skills do you have?" - not every turn.
        said = " ".join(i.text for i in batch if getattr(i, "kind", "") == "human")
        about_skills = (skillbook.for_router(skillbook.load_all(self.config.skills_dir))
                        if re.search(r"\bskills?\b", said, re.I) else "")
        if not picked:
            return about_skills
        for m in picked:
            self._shown.add(m.id)
            m.used_ts, m.uses = time.time(), m.uses + 1
            self.store.put_memory(m, quiet=True)
        return memory.for_router(picked) + (f"\n{about_skills}" if about_skills else "")

    def _session_for(self, args: dict[str, Any], loop: AgentLoop) -> Any:
        thread = self.store.threads.get(str(args.get("thread_id") or "").strip())
        if thread is None:
            thread = self.store.threads.get(self._session_of_this_turn(loop) or "")
        return thread if thread is not None and thread.channel in self.config.conversation_channels else None

    def _show_in_chat(self, thread: Any, text: str, **extra: Any) -> None:
        if thread is not None:
            self.store.add_chat("memory", text, chat=thread.key, **extra)

    async def _tool_remember(self, args: dict[str, Any], loop: AgentLoop) -> str:
        text = " ".join(str(args.get("text") or args.get("note") or "").split())
        if not text:
            return "Nothing to remember."
        if memory.looks_secret(text):
            return "Not remembered: it looks like a secret (a key or a password). Those are never kept."
        about = str(args.get("about") or "general").strip().lower()
        about = about if about in {"you", "session", "general"} else "general"
        thread = self._session_for(args, loop)
        key = thread.key if (about == "session" and thread is not None) else ""
        if about == "session" and not key:
            about = "general"
        for old in self.store.memories.values():
            if memory.same(old.text, text) and old.about == about and old.session_key == key:
                return f"Already remembered [{old.id}]."
        kept = self.store.put_memory(Memory(id=new_id("memory"), text=text[:500], about=about,
                                            session_key=key))
        self._shown.add(kept.id)                 # it is in this turn already
        self._show_in_chat(thread, kept.text, memory_id=kept.id)
        return f"Remembered [{kept.id}] ({memory.ABOUT[about] or 'general'})."

    async def _tool_forget(self, args: dict[str, Any], loop: AgentLoop) -> str:
        gone = self.store.delete_memory(str(args.get("memory_id") or "").strip())
        if gone is None:
            return "No such memory. Nothing was forgotten."
        self._show_in_chat(self._session_for(args, loop), gone.text, forgot=True)
        return f"Forgot [{gone.id}]: {gone.text}"

    async def _tool_thread_context(self, args: dict[str, Any], loop: AgentLoop) -> str:
        thread = self.store.threads.get(str(args.get("thread_id") or "").strip())
        if thread is None:
            return "No such thread."
        return inline_thread(thread, self.store, cap=self.config.inline_content_cap, recent=10)


def _why(failure: object) -> str:
    """A failed call, in words a person understands."""
    text = str(failure)
    if "429" in text:
        return "too many requests"
    if re.search(r"HTTP 5\d\d", text):
        return "the service has a problem"
    if any(word in text for word in ("Timeout", "Connect", "Transport", "Network", "Read")):
        return "it can't be reached"
    found = re.search(r"HTTP (\d{3})", text)
    return f"error {found.group(1)}" if found else "an error"
