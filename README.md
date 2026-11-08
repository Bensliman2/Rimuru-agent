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
- **Free models.** No account at all: **Kilo Code** serves free models with no key
  (about 200 requests an hour). Free keys from Groq, Google Gemini, Mistral,
  OpenRouter, NVIDIA, Z AI and Cohere are one paste away in **Models** (from the list
  at [awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)). Free
  tiers may log what you send - nothing confidential.
- **Running code.** Start **Docker Desktop** before Rimuru and clones can run what
  they write - tests, scripts - inside a sandbox (below). The sandbox image builds by
  itself the first time (about a minute). Without Docker, clones can still read and
  write files but cannot run anything, and they say so.

### Or in Docker

```bash
docker compose up -d
```

Then open **http://localhost:8800** - the same app, on this computer only. Two
containers: Rimuru, and a Docker of its own (`sandbox`) where each clone's commands run
sealed - no internet, only that job's folder. Everything is kept next to
`docker-compose.yml`:

| Folder | What is in it |
|---|---|
| `rimuru-data/` | chats, jobs, memories, session folders, your models and API keys |
| `skills/` | your skills - edit them there or with the Skills button |
| `projects/` | projects for Rimuru to work on: pick **Projects** when you click **Change** above the chat |

`docker compose logs -f rimuru` shows what it is doing; `docker compose down` stops it
and keeps your data. The `sandbox` container runs *privileged* (Docker inside Docker
needs it) but only the app can reach it, over compose's private network, with TLS. On
Linux, files clones make in `projects/` belong to root.

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
- **One session per piece of work.** Click **New session** for each new thing you
  want done. Every session keeps its own conversation, its own clones and its own
  folder of files, so nothing gets mixed up - and Rimuru still remembers what you
  told it in other sessions.
- **You choose where each session works.** The bar above the chat shows the
  session's folder. **Change** picks a folder on your computer - a project you
  already have, or a new folder you make right there - or leaves it to Rimuru,
  which makes one named after the session's first job (`data/sessions/<name>`).
  **Open** shows it in File Explorer. A whole drive, your whole home folder, the
  system's folders and Rimuru's own folder are refused, and clones are told never
  to delete files of yours they did not make.
- **/status: how is it going?** Type `/status` in a session (or on Telegram) for an
  instant answer from the records - who is working on what, what is done, what did
  not finish. No AI call, so it costs nothing and works even while Rimuru is busy.
- **/init: a guide to your project.** In a session that works in your own project,
  type `/init` (typing `/` shows the commands). A clone studies the folder and writes
  `RIMURU.md` - what the project is, how it is organized, how to run and test it, its
  conventions. From then on every clone that works there reads it first, the way
  Claude Code reads `CLAUDE.md`; a folder with an `AGENTS.md` or `CLAUDE.md` of its own
  works too. Run `/init` again to bring the guide up to date. It works from Telegram too.
- **Skills: your how-tos, for every clone.** The **Skills** button keeps how-tos you
  write once - your code style, how you test, how you deploy. Each is its own file in
  `skills/` (edit them by hand if you like; `skills/README.md` shows the format). Every
  clone sees the list - each skill's name and when to use it - and reads one in full
  only when it fits its job, so many skills cost almost nothing. To make sure a job
  follows one, type it before your message: `/skill_web_pages build a landing page`
  (typing `/` lists them). Turn one off to keep it
  without using it; a clone's details show which of your skills it used. Skills are
  instructions, not code: they give a clone no new powers. Keep passwords and keys out.
- **The clone tells you itself.** When a job from your chat is done, the clone that did
  it writes you the message - in your language, with what you now have and how to use
  it. That saves a call to the AI and its wait; Rimuru just gets a note.
- **Follow-ups go to the same clone.** A new job within 10 minutes of the last one in a
  session goes to the clone that just finished it, which remembers the work instead of
  rediscovering it (measured on free models: 15 seconds and 2 AI calls, against 103
  seconds and 5 for a new clone). After that, a new clone starts - and it is told what
  the earlier ones did. Each clone's card shows how many AI calls and tokens its job took.
- **Before the free limit, not after.** On a free plan with a known limit, the status at
  the top counts this hour's requests, and Rimuru says so in the chat when you are close.
  **Models** has a ready-made free setup: a quick model for Rimuru, a stronger one for
  the clones, in one click.
- **Every clone has a name, a persona and a color** - Mika the amber one, Dax the mint
  one - and the clones working side by side never share a color.
- **Rimuru remembers.** It keeps lasting facts by itself - about you, about each
  session's work - and uses them in later sessions without being asked; its clones get
  them too. A small line in the chat shows each one as it is kept, and Under the hood ->
  Memory lists them all, to change or forget. Passwords and keys are never kept.
- **Interrupt any time.** If Rimuru is busy with something else when you write - a
  clone's report, a group chat - your message cuts in and is answered first.
- **If your AI provider runs out of credit** or rejects the key, Rimuru says so in
  the chat and in the status at the top, keeps trying quietly, and carries on by
  itself once it works again - no need to ask twice.
- **Restarts lose nothing.** The chat, every job and its history come back - and so
  does what Rimuru and each clone had in mind, saved after every step. A job that was
  running carries on exactly where it stopped, as the same clone, and Rimuru tells you
  so. One that keeps getting cut off is stopped after 3 resumes (or at once if it got
  nowhere since the last one) rather than looping.

## Clones can run code - safely

When Docker is running, every command a clone runs gets a throwaway container:

| | |
|---|---|
| network | **none** - nothing can be downloaded, and nothing can leave |
| files | only the session's own folder is visible; the rest of your disk is not |
| system | read-only; every Linux capability dropped; no privilege escalation |
| limits | 1 GB memory, 2 CPUs, 256 processes |
| runaways | killed at the timeout or on Stop - the container itself, not just the client |

Each of these properties has an automated test (`tests/test_sandbox.py`).
`RIMURU_SHELL=host` runs commands directly on your machine instead - unsandboxed, so
only use it knowingly.

**Your keys stay yours** (`rimuru/safety.py`, `tests/test_secrets.py`):

- Clones never open a file that holds secrets (`.env`, `id_rsa`, `*.pem`, `.ssh/`...) and
  never overwrite one. Their commands don't see those files either: in the sandbox an
  empty file stands in for each.
- No key reaches an AI service or leaves the app: every tool result and every reply is
  checked - for the keys you actually have set, and for the usual key formats - and a
  key is replaced by `[hidden key]`. A clone can't write that stand-in back over the real
  key.
- A pull request never includes a file with a key in it; Rimuru says which it left out.
- A key pasted into the chat is hidden before it is stored or sent, and Rimuru tells you.

## Connections: Telegram and GitHub

The **Connections** button (next to Models) connects Rimuru to the outside. Only the
people you allow can give it work; tokens go into your `.env` and are never shown again.

**Telegram - talk to Rimuru from your phone.** Connections walks you through four
steps, each ticking itself off: make a bot with @BotFather (`/newbot`), paste its
token, say hi to your bot, allow yourself. Each private chat is a session like any
other (listed in the app with a Telegram mark, with its own folder and clones), and
Rimuru's answers go back to Telegram - in plain words: it just does the job, and sends
code or files only when you ask ("send me the files"). `/status` works there too.
Anyone else gets "this is a private assistant" - once. Groups are off unless you
switch them on, and even then only allowed people are heard.

**GitHub - issues and pull requests.** Make a fine-grained token for just the
repositories you want (Contents, Issues and Pull requests: read and write), paste it,
and list the repositories. When someone you allow writes `@rimuru` on an issue or a
pull request, Rimuru answers there, signed as an AI assistant. For a code change, a
clone works in a copy of the repository on its own branch (`rimuru/issue-N`), and
when it is done Rimuru opens a **draft** pull request and links it in the thread. It
never merges. The copy's git folder is kept out of the clone's reach, so nothing a
clone - or a hostile repository - writes can run on your computer through git.

**Other machines - the remote hands.** A session can work in a folder on another
computer - a server, a second PC - while Rimuru keeps thinking on this one. In
Connections, click **Add a machine**: it shows a command with a one-time code. On the
other machine (with Rimuru's folder copied there and `pip install -r requirements.txt`
done), run it with the folder to share:

```bash
python run.py hands --connect http://<this PC>:8810 --code ABCD2345 --root C:\work
```

It connects out to Rimuru - nothing opens on that machine - and from then on a
session's **Change** offers "A folder on another machine". Over there, files are
read and written only inside the shared folders, and commands run in that machine's
own Docker sandbox (none at all without Docker, unless it was started with
`--allow-host-commands`). Pairing codes work once, for 10 minutes; Rimuru keeps only
a hash of each machine's secret; **Remove** cuts a machine off. Rimuru listens for
machines on a port of its own (8810, `RIMURU_HANDS_PORT`), which answers nothing but
them and refuses any web page; the app itself still answers this computer only. Use
it on your own network, or across the internet only through a private network such
as Tailscale - never an open port.

## Under the hood

**Under the hood** (the link at the top of the app) shows how Rimuru works, live, in
the same plain language as the chat - one view at a time:

| View | What you see |
|---|---|
| Overview | Rimuru, the clones and the watchdog at a glance; what came in; the latest events; models and cost |
| Rimuru | what it is doing, what it decided and said, and its thoughts turn by turn - all of them, or only those about one session (a turn that handled two sessions shows under both). **Clear** clears turns from this log only: Rimuru keeps what it has in mind and the chat stays; to forget a session completely, delete the session |
| Clones | every clone with its little slime, grouped by session; pick one to see its job, its commands and every turn |
| Watchdog | its checks and interventions, and its notes - a quiet supervisor |
| Messages | every message that came in - your chat, Telegram, GitHub, practice runs - and what became of it, kept across restarts; and the rules for group chats, editable in plain words |
| Memory | find anything by typing a word or two - a session, a job, a message - and everything Rimuru remembers, to change or forget; and how long old jobs keep every detail |
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

**Speed.** Clones build the smallest version that works first and offer extras
afterwards - the only way they build. How fast they are depends mostly on the clones'
model. "Build a calculator with
a web UI" took **14.6 minutes** the old way on `kimi-k3.1-pro`, 5.9 with the quick
style on the same model, and **1.9 minutes** with the quick style on
`kimi-k2.7-code-highspeed` - and every version worked. Details and the model
recommendation: [docs/speed.md](docs/speed.md).

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
python -m pytest                          # every check, including sandbox safety
python run.py --demo                      # no AI model at all - an offline stand-in
python run.py bench --scenario team       # the article's 300-event workload, headless
python run.py bench --scenario smoke --speed 1   # real-model bug fixing on your models, graded
python run.py verify build                # grade a calculator/converter build
python run.py trace --db data/rimuru.db   # one request's journey, step by step
python run.py eval run                    # the end-to-end evaluation (docs/evaluation.md)
python run.py speed probe                 # how fast each of your models writes
python run.py speed run --label NAME      # three everyday requests, timed and checked (docs/speed.md)
python run.py scale                       # restart time, memory and page refresh, up to 5,000 jobs
```

`scenarios/` holds the simulated workloads, a small repo with real bugs, and the hidden
acceptance tests; Under the hood replays the workloads as practice runs. Models are
set in `config/models.toml` (see `config/models.example.toml`); keys and the other
settings are listed in `.env.example`. The app answers only this computer: requests
from other websites and other host names are refused, because it stores API keys.

## Honest limits

What each of the old limits is now, and what is still true:

- **Hidden instructions are reported, not only resisted.** The person is told, in the
  session's chat, where text aimed at AI tools was found and what it said. The
  detector is narrow on purpose (no false alarms on ordinary docs about AI), so a
  cleverly worded instruction can go unreported - the clone still treats it as content.
- **Cutting in costs little now.** Rimuru's calls stream, so a cut-in stops the answer
  being written, and Under the hood shows what was thrown away. Measured live (Kilo, a
  free model): cut in at 2 s, about 95% of the answer was never written. What the
  provider bills is not visible from here; a provider that cannot stream falls back
  to a normal call, which may still be billed in full.
- **Interrupted work resumes exactly where it stopped,** as the same clone, up to 3
  times. A command that was cut off mid-run is not re-run by itself - the clone is told
  and decides; a model call that was in flight is asked again.
- **Telegram and GitHub are connected; Discord, Slack and email are not.** GitHub is
  read every minute (no webhooks), so an answer can take up to a minute to start;
  Telegram reads text only; pull requests are drafts and are never merged.
- **The remote hands are built - the brain in the cloud is not.** Rimuru itself must
  stay on your computer: the app has no login, so it is never served on the internet.
  The hands were tested end to end with two programs on one PC, not yet between two
  real machines.
- **Long-term memory matches words, not meaning.** A memory comes back when the turn
  shares words with it (or when it is about you or the session); something that was
  only true for a while stays until it is changed or forgotten.
- **The bigger toolbox and on-demand installs are not yet checked live:** Docker
  Desktop was not running, so the image build and the two sandbox tests for them wait
  for it. Python packages must have ready-made versions (wheels); nothing is built
  from source.
- **When a free AI service's limit runs out,** Rimuru keeps retrying quietly, and the
  person is not yet told why it went silent (found in the user test).

## Layout

```
rimuru/
  router/        the Router (Rimuru), the filter, routing
  worker/        clones, the pool, files.py (a clone's files) and shell.py (the sandbox)
  watchdog/      the external control loop
  agent/         the asynchronous loop and the tool registry
  channels/      chat channels and manners: Telegram, GitHub, simulated scenarios
  hands/         the remote hands: a session working on another machine
  llm/           catalog.py (which model does which job), Kimi / OpenAI-compatible,
                 Claude, and the offline demo model
  web/           the chat app (static/index.html) and Under the hood (/inside)
  context.py     Inline & Spill
  recall.py      Chain of Recall
  store.py       durable state: chat history, saved conversations, memories
  memory.py      long-term memory: what is kept, and what comes back when
  safety.py      finding hidden instructions aimed at AI tools
  connections.py who may give Rimuru work on Telegram and GitHub
  folders.py     picking a session's folder, safely
  commands.py    commands typed in the chat: /status, /init, and the project guide clones read
  skills.py      your skills: one file each, listed for every clone, read when they fit
config/          models.toml - your models (made by the Models button), and an example
data/            your own data, never committed:
  rimuru.db        everything Rimuru remembers - chats, jobs, every step
  sessions/        Rimuru's folder for each session that did not pick its own
  practice/        where practice runs play
  github/          working copies of your repositories, for GitHub work
  deps/            packages clones installed, one set per work folder
skills/          your skills, one Markdown file each - yours, not committed (see its README)
sandbox/         the container image clones run code in
docs/            evaluation.md - the end-to-end evaluation and what to fix
scenarios/       test workloads, a demo repo with real bugs, hidden acceptance tests
tests/           232 automated checks
run.py           serve | bench | verify | trace | eval | speed | scale | hands
```
