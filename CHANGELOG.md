# Changelog

What changed in each version. Versions follow [Semantic Versioning](https://semver.org);
before 1.0, a minor version may change how things work, and this page says how.

## [Unreleased]

### Added

- **The session notebook.** Clones working on the same session write what they find out
  there, with how they know it (`note_finding`, `read_notebook`). The clones at work hear
  of each new finding, new clones read the notebook in their brief, and `recall(fnd_...)`
  reaches any finding. A clone must say whether a finding on a subject already covered
  confirms, contradicts or settles it; settling needs evidence from a check. Rimuru and
  the other clone are told of a contradiction, a clone with one still open is asked once
  to keep both sides before it finishes, and Rimuru gets both sides with the result.

### Fixed

- A finished job's summary was given to Rimuru twice in the same message.

## [0.1.0] - 2026-11-11

The first public version, free software under the GNU AGPL-3.0
(Copyright (C) 2026 Hamza Bensliman and Anas Dadi).

### What you can do

- Chat with Rimuru in your browser, or on Telegram. Each job goes to a clone that writes
  the code, runs it in a sealed Docker sandbox, checks it, and tells you in plain words
  what you now have.
- **View it.** What a clone builds opens from a link in its message and on its card,
  served on a port of its own so a built page can never reach the app. **Download** gives
  the session's folder as a zip. Apps with a server (Flask, FastAPI, Node) run in a preview
  container that sees only their folder, reachable only from this computer.
- Free models with no account (Kilo Code), a key you paste (Kimi, Claude, OpenAI,
  DeepSeek, Groq, Gemini, Mistral, OpenRouter and other OpenAI-compatible services), or
  Ollama on your own computer. A backup model takes over when the main one keeps failing.
- The interface in English, French and Arabic, right to left in Arabic. Rimuru and its
  clones answer in the language you write in.
- Sessions with their own folders, follow-ups that go to the same clone, long-term memory
  you can see and edit, skills, `/status` and `/init`.
- Under the hood: every agent, job, step and message, in plain words.

### Reliability

- No message is lost. A failed AI call is retried with a growing wait, never dropped, and
  you are told at once when the service is failing. Waiting messages survive a restart.
- Bounded by design: at most 8 tool rounds per Rimuru turn and 3 per watchdog check, an
  hourly request limit (`RIMURU_MAX_REQUESTS_PER_HOUR`), a time limit per job
  (`RIMURU_JOB_MINUTES`). Your chat goes before the clones, and the clones before the
  watchdog.
- Rimuru makes one AI call per message on average, measured on a free model.
- Interrupted clones resume where they stopped after a restart, up to 3 times.

### Safety

- Every command a clone runs gets a throwaway container: no network, only the job's
  folder, read-only system, capabilities dropped, memory, CPU and process limits.
  `RIMURU_SANDBOX_RUNTIME=runsc` adds gVisor.
- Keys never reach a model, a reply, a pull request or the log.
- Instructions hidden in files or messages and aimed at AI tools are reported to you, in
  English, French and Arabic.
- An optional password (`RIMURU_PASSWORD`) for reaching Rimuru from your phone over a
  private network. See [SECURITY.md](SECURITY.md).

### For the person who runs it

- `python run.py export`, `import` and `wipe`. The database is backed up before an
  upgrade changes it.
- `/api/health`, **Report a problem** in Under the hood (the log with keys removed, no
  chats or files), and `RIMURU_LOG_LEVEL`.
- Pinned dependencies (`requirements.lock`). Each version tag publishes the app's and the
  sandbox's images.
- `python run.py golden`: ten everyday requests, run for real and graded. 10 of 10 passed
  on Kilo's free models for this version.

### Experimental

- GitHub (issues to draft pull requests) and remote hands (a session working on another
  machine) were tested with simulated services and on one computer only.
