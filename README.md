# Rimuru

Chat with one assistant. Behind the scenes it splits into **clones** that work on
your requests in parallel - writing code, running it in a sealed sandbox, checking
their own tests - and reports back in plain language.

It is a working implementation of the **Super-agent** architecture from the DeepSeek
Harness team's article [*Exploring Super-agent Design: Towards a More Autonomous Agent
System*](https://x.com/DeepSeekHarness/article/2116857257245987236): one continuous
assistant you talk to, many isolated workers doing the jobs, and an independent
watchdog keeping them honest.

## Start

```bash
pip install -r requirements.txt
python run.py
```

Then open **http://127.0.0.1:8800**.

- **Your models.** Click **Models** in the app and choose which AI does each job:
  Rimuru itself, the clones, the watchdog and the noise filter. Kimi, Claude, OpenAI,
  DeepSeek, Ollama (on your own computer) and any OpenAI-compatible server work. Paste
  a key and **Find models** lists what it can use. Nothing about models is fixed in
  the code: the choice is saved in `config/models.toml`, which you can also edit by
  hand (`config/models.example.toml` shows every option). Keys are kept in `.env` on
  your computer and never shown again. Until models are set up, Rimuru runs in
  **demo mode**: it shows how everything moves, and says plainly that nothing real
  was built.
- **Running code.** Start **Docker Desktop** before Rimuru and clones can run what
  they write - tests, scripts - inside a sandbox (below). The sandbox image builds by
  itself the first time (about a minute). Without Docker, clones can still read and
  write files but cannot run anything, and they say so.

## Using it

- **Type what you need** in the chat. Rimuru answers in plain words and tells you
  when it has started on something.
- **Clones at work**, on the right, shows each job: what it is, what it is doing now,
  how long it has taken, the files it wrote and whether its tests pass. Hover a clone
  for a summary; click it for the full story - a four-step strip (*Looked at the code,
  Made the changes, Tested it, Reported back*) where a small slime shows what the clone
  is doing right now, then what it was asked, its result, the files it changed and
  every step in plain sentences, with the raw technical detail folded away.
- **Add details any time.** Say "also make it do X" while a clone is working and
  Rimuru passes it to the right clone.
- **Stop** stops everything in progress. A single job can be stopped from its details.
- **Interrupt any time.** If Rimuru is busy with something else when you write - a
  clone's report, a group chat - your message cuts in and is answered first.
- **Restarts lose nothing.** The chat, every job and its history come back. A job that
  was running is picked up again, once, and Rimuru tells you so; if it is cut off a
  second time it stops and says so rather than looping.

## Clones can run code - safely

When Docker is running, every command a clone runs gets a throwaway container:

| | |
|---|---|
| network | **none** - nothing can be downloaded, and nothing can leave |
| files | only the workspace (`data/workspace`) is visible; the rest of your disk is not |
| system | read-only; every Linux capability dropped; no privilege escalation |
| limits | 1 GB memory, 2 CPUs, 256 processes |
| runaways | killed at the timeout or on Stop - the container itself, not just the client |

Each of these properties has an automated test (`tests/test_sandbox.py`).
`RIMURU_SHELL=host` runs commands directly on your machine instead - unsandboxed, so
only use it knowingly.

## Under the hood

**Under the hood** (the link at the top of the app) shows how Rimuru works, live, in
the same plain language as the chat - one view at a time:

| View | What you see |
|---|---|
| Overview | Rimuru, the clones and the watchdog at a glance; what came in; the latest events; models and cost |
| Rimuru | what it is doing, what it decided and said, and its thoughts turn by turn |
| Clones | every clone with its little slime; pick one to see its job, its commands and every turn |
| Watchdog | its checks and interventions, its notes - and a box to ask it why something failed |
| Messages | what happened to every incoming message, and the rules for group chats, editable in plain words |
| Memory | the notes Rimuru keeps, and a lookup for any ID |
| Practice runs | scripted workloads - a busy team chat, a burst, a quiet day, two coding exercises |

Five modules, each a section of the article:

| Article | Module | What it does |
|---|---|---|
| s2.1 | `router/session.py` | **Router** - the "Rimuru" you talk to. One long-lived session that talks to people and hands out work. It has no shell and no file tools, so it *cannot* do the work itself. |
| s2.2 | `worker/` | **Workers** - the clones. One job each, isolated context, full tools. They report to the Router, never to people. Over the limit, jobs queue. |
| s3.1 | `router/filtering.py` | **Two-stage filter** - code drops noise first; a model judges only what survives, against policies editable in plain language. |
| s3.2 | `router/routing.py` | **Pass-through** - in group channels a worker follows its thread, so follow-ups reach it directly. In the one-to-one chat the Router owns the conversation and relays follow-ups itself. |
| s3.3 | `context.py`, `recall.py` | **Inline & Spill** and **Chain of Recall** - everything has an ID; whatever is trimmed from a context window stays reachable. |
| s4.1 | `agent/loop.py` | **Async loop** - `start()` returns at once and results arrive as events, so nothing blocks; events that pile up are merged into one turn. A person's message cuts in on a turn that something else started. |
| s5.1 | `store.py`, `system.py` | **State outside the loop** - every record is in SQLite and reloaded on start: chat, jobs, transcripts, notes. A job cut off by a restart is resumed once, with a note of what it had already done. |
| s6 | `watchdog/session.py` | **Watchdog** - checks that clones make *progress*, not just that they are alive, and nudges the ones going nowhere. Idle costs nothing. |

## Proven on a real model

Kimi, November 2026 - Router on `kimi-k3.1-pro-highspeed`, clones on `kimi-k3.1-pro`.
Every result is judged by tests the agent could not see or change, never by what it
says.

| Test | Result |
|---|---|
| Fix 2 real bugs from a chat stream | **PASS** - original tests 5/5 against its code |
| Build a calculator and a unit converter in parallel | **PASS** - hidden acceptance tests 37/37 |
| Same, typed into the chat app, clones running code in the sandbox | **PASS** - hidden tests 37/37; its own 134 tests pass; it ran its CLI to check itself; Rimuru relayed a mid-task follow-up to the right clone |
| **Under pressure**: a messy morning - parallel clones on one file, a change of mind, cancel, secrets, prompt injection, a crash | First run: 11 pass, 1 fail, and the crash probe failed. **After the fixes: 13 pass, 0 fail, 3 warnings; the crash and Stop probes pass** - see [docs/evaluation.md](docs/evaluation.md) |

Things only a real model surfaced, all fixed:

- A reasoning model spent the filter's whole token budget thinking, the answer came
  back empty, and a real bug report was silently dropped. Only an explicit IGNORE
  drops an event now.
- A clone took 259 seconds to write a parser in one go - past the old 180s read
  timeout, which retried silently from scratch. The timeout is 10 minutes and every
  retry is logged.
- Without a shell, a clone wrote a test asserting `2E+2 // 3 == 33` (it is 66) and
  could not notice. With the sandbox, its own tests are run - and right.
- With one shared chat thread, the first clone would have captured everything typed
  afterwards. The Router now owns the chat and relays follow-ups.
- The Watchdog read a long generation as "no progress" and nudged a healthy clone; it
  is now told when a model call is in flight.
- Two clones edited the same test file, and the second write - built on an older read -
  deleted the first clone's new test. A clone's write now carries the version it read
  and is refused if the file changed since; clones make targeted edits with
  `edit_file`. In the re-run, one conflicting edit was refused and redone, and nothing
  was lost.
- A crash erased the running job without a word. Jobs now resume after a restart.
- "I'll create the manual" went out after the person had said "cancel the manual".
  A reply is now held when the person changes course while Rimuru is thinking, and
  chat messages no longer wait on the noise filter. The median first reply went from
  17s to 8s, and the slowest from 36s to 25s.
- That 25s was a status question waiting behind a 28-second turn nobody needed. A
  person's message now cuts in on such a turn and is answered first.

## For developers

```bash
python -m pytest                          # 48 checks, including sandbox safety
python run.py --demo                      # no AI model at all - an offline stand-in
python run.py bench --scenario team       # the article's 300-event workload, headless
python run.py bench --scenario smoke --speed 1   # real-model bug fixing on your models, graded
python run.py verify build                # grade a calculator/converter build
python run.py trace --db data/rimuru.db   # one request's journey, step by step
python run.py eval run                    # the end-to-end evaluation (docs/evaluation.md)
```

`scenarios/` holds the simulated workloads, a small repo with real bugs, and the hidden
acceptance tests; Under the hood replays the workloads as practice runs. Models are
set in `config/models.toml` (see `config/models.example.toml`); keys and the other
settings are listed in `.env.example`. The app answers only this computer: requests
from other websites and other host names are refused, because it stores API keys.

## Honest limits

- **Hidden instructions are resisted, not reported.** A README telling AI tools to
  delete the tests was ignored, but the owner was never told it is there.
- **Cutting in has a cost.** When your message interrupts Rimuru, the AI call it
  abandons may still be billed by the provider. The fix was tested offline; it has not
  yet been measured in a live run.
- **A resumed job restarts from a summary** of what it had done, not from the exact
  point it stopped, and only once.
- No connections to real Discord, Telegram or GitHub yet - the channel interface is
  ready for them.
- The remote endpoint daemons of article s5.2 (running the brain in the cloud and the
  hands on your machines) are not built.
- No long-term memory beyond the chat history and the Router's notes (s8.1).
- Inside the sandbox nothing can be installed (no network), so clones work with Python
  and pytest only.

## Layout

```
rimuru/
  router/        the Router (Rimuru), the filter, routing
  worker/        clones, the pool, and shell.py (the sandbox)
  watchdog/      the external control loop
  agent/         the asynchronous loop and the tool registry
  channels/      chat channels, manners, simulated scenarios
  llm/           catalog.py (which model does which job), Kimi / OpenAI-compatible,
                 Claude, and the offline demo model
  web/           the chat app (static/index.html) and Under the hood (/inside)
  context.py     Inline & Spill
  recall.py      Chain of Recall
  store.py       durable state, including chat history
config/          models.toml - your models (made by the Models button), and an example
sandbox/         the container image clones run code in
docs/            evaluation.md - the end-to-end evaluation and what to fix
scenarios/       test workloads, a demo repo with real bugs, hidden acceptance tests
tests/           48 automated checks
run.py           serve | bench | verify | trace | eval
```
