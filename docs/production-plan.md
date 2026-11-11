# From working prototype to a product people can use

Proposed on 2026-11-11 and started the same day. The owner chose open source, so phases 4
(hosted) and 5 (charging) are not planned for now.
When a phase lands, change its status line and note the commit.

## Verdict

The engine is a faithful, well-tested build of the Super-agent article. It is not a
product yet, for three reasons:

1. It can lose a person's message, and it can use up a model's limit by itself.
2. It stops one step short: people get a folder of files, not something they can open.
3. It is a single-user app for one computer, with no versions, limits or upgrade path.

None of this needs a rewrite. The design holds; the gaps are specific and listed below.

## Evidence

Measured on 2026-11-11 from this repository and from the owner's own usage data
(`data/rimuru.db`, `data/usertest/rimuru.db`).

| Finding | Evidence | Where |
|---|---|---|
| The watchdog runs away | 87 AI calls within minutes, 0.7 s apart (median), every one "healthy". It used 151,000 input tokens. The clones that built the app used 48,000. | `watchdog/session.py`: `_tool_assess` returns text, so the model is called again. `max_steps=0`. |
| A failed AI call drops the turn | 3 of the owner's messages on 2026-11-08 never got an answer (HTTP 429 after 6 tries). | `agent/loop.py` `_run`: the plain `except Exception` branch waits for a new event. Only account errors are retried. |
| Waiting messages die with the process | The inbox is a queue in memory only. | `agent/loop.py` `inject` |
| Rimuru makes calls that do nothing | 13 of 31 calls only read "sent" after a reply or a dispatch. | `agent/loop.py`: every tool result costs another model call. |
| No limits | No time limit per job, no disk quota, no spending cap, no priority for the person's chat over the background agents. | `config.py`, `worker/pool.py` |
| Nothing shows the result | No preview route and no preview tool. The owner's own session ended with "I can't actually run the containers here". | `web/routes/` |
| Single user by construction | No login. Keys are written into the process environment. One Router, one database. | `llm/catalog.py`, `web/routes/first_run.py`, `connections.py` |
| Not packaged | No licence, version, changelog, pinned dependencies, schema version or backup. | repository root, `store.py` |
| English-only interface | Buttons, `/status`, the Telegram welcome. | `web/static/`, `commands.py` |
| Big functions | `AgentLoop._run` is 246 lines. `RimuruSystem` is 883 lines with 40 methods. `hood.add_routes` is 383 lines. | |
| Weight nobody uses | About 15% of the code (2,099 of 14,029 lines: noise filter, thread routing, practice runs, GitHub, remote hands) serves the article's team scenario. No real user has run it. Telegram and GitHub were never run with real tokens. | |

What is good and stays:

- State lives outside the loops. Restart takes 0.13 s at 1,000 jobs, and clones resume where they stopped.
- Safety: a sealed sandbox, keys never reach a model, hidden instructions are reported.
- 280 tests, with a real browser and the real Docker stack on every push.
- Clean boundaries: one import cycle, and only three modules touch the global settings.
- One idle Rimuru costs 63 MB and wakes in about 4 seconds on this PC. That makes
  "one sleeping instance per person" a cheap way to host many people later.
- A job costs roughly 1 to 2 cents on a cheap paid model: 39,000 tokens in and 4,900 out
  per job in real use. Prices are from third-party lists that disagree, not from DeepSeek's own page.

## The market on 2026-11-11

- **DeepSeek published its own prototype of this design** on 2026-10-31:
  `deepseek-ai/dsh-superagent-prototype`, MIT licence, 22,394 stars in 11 days. It is a
  coding agent for one GitHub repository and a team. It needs DeepSeek Harness, its setup
  has many steps, and its deployment guide says it runs "with danger-full-access as the
  operator's OS user". So the architecture is free for anyone, and is no longer an
  advantage by itself.
- **Lovable has a Telegram bot** that builds and publishes apps. Pro is $25 a month and
  the free plan has 5 credits a day (third-party pricing pages).
- **OpenClaw** leads self-hosted assistants in chat apps (about 359,000 stars reported).
  It was renamed twice after a trademark complaint.
- **Arabic:** GoDaddy's Airo claims full right-to-left support. A guide for MENA founders
  says Lovable's Arabic is machine-translated.

What this project has that the official prototype does not: it stands alone, works with
any model including free ones, seals what clones run, protects keys, has a plain-language
interface and Telegram, and starts with one command.

## Direction

**Recommended: release it as open source while interest in the article is high, as the
safe Super-agent anyone can run. Build the hosted version second, and only if people use
the first.**

Why: the biggest gap is that nobody but the owner has used it. A public release is the
cheapest way to get real users, costs nothing to run, and the code holds no secret now
that DeepSeek's own is public. A hosted product first would take months and money before
anyone sees it, and it would face Lovable's features on day one.

The alternative is to keep it private and go straight to a hosted product for one
audience. Phases 0 to 2 are the same either way, so this choice can wait until phase 3.

## The plan

Effort is in days of working sessions. These are estimates, not promises.

### Phase 0: never lose a message, never run away (2 to 3 days)

Status: done on 2026-11-11 (`c959431`). Fault tests: `tests/test_reliability.py`. Measured in the
golden run below (real free model): 1.0 Rimuru call per message, 3 watchdog calls in ~7 minutes,
no failed call.

1. **Tools that end the turn.** After `assess` (watchdog) or `reply`, `ignore`,
   `dispatch_task`, `message_worker`, `send_files`, `remember`, `forget`, `stop_task`
   (Rimuru) succeed and nothing new is waiting, the loop does not call the model again.
   The results go in with whatever wakes it next. A tool that failed still wakes the model.
2. **Retry, never drop.** When an AI call fails after its retries, keep the turn and try
   again with a growing wait, the way account errors already do.
3. **Say so at once.** If a person's message has waited more than a few seconds because
   the AI service is failing, code posts one line in the chat: the message is safe and
   will be answered. No silence.
4. **A saved inbox.** Messages waiting for an agent are stored and handed over again
   after a restart.
5. **Caps.** At most 8 tool rounds per Rimuru turn and 2 per watchdog tick. A time limit
   per job. A requests-per-hour budget with a visible meter.
6. **The person goes first.** One shared limiter per AI service, with priority: the
   person's chat, then clones, then the watchdog and the filter.
7. **A second model.** If the chosen model fails twice, the job tries the next one.
8. **Fault tests.** A fake AI service that fails in bursts, stalls, and answers nonsense.
   The tests assert: no lost message, the person is told, calls stay bounded.
9. Split `AgentLoop._run` into its steps while doing this: take in, think, act, settle.

Done when: the fault tests pass. In a 30-minute session on a real free model no message
goes unanswered. The watchdog makes at most one call per tick. Rimuru averages at most
1.2 AI calls per message (2 or more today).

### Phase 1: give people the result, not a folder (4 to 6 days)

Status: done on 2026-11-11. Golden run with Kilo's free models and the Docker sandbox: **10 of 10**
end with something that opens (9 pages, 1 Flask preview), about 40 s and 5 AI requests a job.

1. **View it.** A link in the clone's final message and on its card opens what was built.
   Pages are served from their own address, so a built page can never touch the app's own
   controls. No secret files, no escaping the folder. Plus "Download as zip".
2. **Run it.** A `preview` tool for clones starts the project's server in a preview
   container (only its own folder, reachable only from this computer; it may reach the internet) and returns a link. Python
   and Node first. It stops by itself after an hour.
3. **Clones aim for things that open.** Self-contained pages unless asked otherwise.
   Always end with how to see it. Check it loads before finishing.
4. **Plain limits.** When something cannot run here (Java, a database), one honest
   sentence and what can be done instead.
5. **Golden jobs.** Ten everyday requests run against a real cheap model and are graded
   automatically: `python run.py golden`. Run before every release.

Done when: 8 of 10 golden jobs end with a link that opens. The owner's "gaming platform"
request ends with a working preview or a plain explanation.

### Phase 2: make it shippable (5 to 7 days)

Status: done on 2026-11-11, except: the new name (the owner's call), money per job (dropped with
pricing; requests are capped instead), and the fresh-machine install timing, which needs a
published release.

1. **Identity.** An original name, a licence, version 0.1.0, a changelog.
2. **Install.** CI publishes the app image and the sandbox image, so the first start
   downloads instead of building. Pinned dependencies with a lock file. Upgrade is
   "download and restart".
3. **Data safety.** A schema version, a backup before each upgrade, export and import,
   and "delete everything".
4. **Optional password login** with an HTTPS recipe, so it can run on a small server and
   be reached from a phone. Off by default on a personal computer.
5. **Cost.** Tokens and estimated money per job and per day, with a daily cap.
6. **Security pass.** The threat model written down, a dependency audit, a review of every
   route, an optional stronger sandbox runtime (gVisor), and `SECURITY.md`.
7. **Arabic and French interface**, with right-to-left layout.
8. **Trim.** GitHub and remote hands marked experimental and off. Practice runs behind a
   developer switch. Under the hood reduced to what explains a job.
9. **Health.** A health address, log levels, and a "report a problem" button that bundles
   the log with keys removed.
10. **A README for strangers.** What it is in three lines, a 30-second clip, install,
    first job, an honest comparison with the official prototype, and the limits.

Done when: a fresh Linux machine and a fresh Windows PC go from nothing to a finished
first job in under 10 minutes using only the README. An upgrade from 0.1.0 to 0.1.1 keeps
all data.

### Phase 3: launch and listen (2 to 3 days of work, then two weeks)

Status: the launch kit is done (2026-11-11): README for strangers with screenshots and an
honest comparison with DeepSeek's prototype, CHANGELOG, CONTRIBUTING, issue forms, a "Report a
problem" link, a 57-second explainer video and a LinkedIn draft (`data/launch-kit/`, not
tracked). Making it public, tagging `v0.1.0` and posting are the owner's. The name is still
the owner's call. While recording, a real bug turned up and was fixed: Rimuru answered French
and Arabic messages in English.

1. Make the repository public, publish the release, post the launch. Drafts by Claude,
   posted by the owner.
2. Answer every issue within a day for two weeks.
3. Count image downloads, stars, issues and, only if people opt in, whether a first job finished.

Gate: after two weeks, decide on phase 4. Suggested bar: at least 20 people ran three or
more jobs. Below that, fix what stopped them before building anything hosted.

### Phase 4: a hosted version, nothing to install (4 to 6 weeks)

Status: not started. Only after the phase 3 gate, or if the owner chooses the hosted path.

The core stays as it is. A layer goes around it:

- Sign in with Telegram. One Rimuru per person, asleep when idle, woken by a message.
- All AI calls go through a metering proxy that holds the keys and enforces each
  person's daily budget.
- One official Telegram bot for everyone.
- Built pages are published on a separate domain, with "report abuse" and takedown.
- A stronger sandbox (gVisor), with CPU, memory and disk limits per person. The features
  that touch the local computer are off.
- Nightly backups, an admin page, a status page, terms and a privacy page.

Done when: five non-technical people each build and share something without help, and a
week of tests shows no lost message and no leak between people.

### Phase 5: charge (1 to 2 weeks)

Pricing from the measured cost per job, payments through a provider that handles tax
(for example Paddle) or Telegram Stars, and a support inbox.

## Frozen until phase 3 is done

No new channels, no new Under the hood views, no new model presets, no work on GitHub or
remote hands. Every hour goes to phases 0 to 2.

## What only the owner can do

- Decide: open source, or private and hosted. Needed before phase 3.
- Pick an original name. The current name and the slime mascot come from an anime that
  someone else owns. That is fine for a personal project and a risk for a public one.
  This is not legal advice; the safe move is a new name before any launch.
- Choose a licence (MIT is the simple choice; it matches DeepSeek's).
- A paid key for a cheap model, for the golden jobs. A few dollars.
- Post the launch and recruit the first testers.
- Later, for phase 4: a domain, a small Linux server, a payment account.

## How we will know it is working

- No unanswered message in a week of real use.
- 8 of 10 golden jobs end with a link that opens.
- A stranger reaches a finished first job in under 10 minutes.
- After launch: how many people run a third job.

## Sources

- https://github.com/deepseek-ai/dsh-superagent-prototype (README and docs/006-deployment.md, read 2026-11-11)
- https://docs.lovable.dev/tips-tricks/lovable-telegram-bot
- https://www.banani.co/blog/lovable-pricing-and-credits
- https://blog.elest.io/openclaw-explained-how-the-fastest-growing-open-source-project-became-a-self-hosted-ai-agent-for-everyone/
- https://pyshine.com/OpenClaw-Personal-AI-Assistant-359K-Stars/
- https://airo-builder.godaddy.com/discover/news/product-update-april-2026
- https://ai-in-arabia-v2.pages.dev/learn/how-to-use-lovable-arabic-web-app-mena-founders-guide
- https://developer.puter.com/tutorials/deepseek-api-pricing/
