# Contributing

Thank you for helping. Rimuru is for people who are not programmers first, so the bar
for a change is: does it make a job easier to ask for, safer to run, or clearer to follow?

## Before you start

- **A bug:** open an issue with what you asked for, what happened, and the file from
  **Under the hood -> Report a problem** (the log with keys taken out, no chats or files).
- **An idea:** open an issue first, so we can agree on it before you spend time on code.
- **A security problem:** not in a public issue. See [SECURITY.md](SECURITY.md).

## Running it from the source

```bash
pip install -r requirements.txt
python run.py --demo          # an offline stand-in model: the whole app, nothing real built
python run.py                 # with real models (the first screen sets them up)
```

Start Docker Desktop first if you want clones to run code; the sandbox image builds by
itself the first time.

## Checks

```bash
python -m pytest                          # every test, about 5 minutes
python -m pyflakes rimuru run.py tests    # unused and undefined names
python run.py golden --url http://127.0.0.1:8800   # ten real jobs, graded (needs a real model)
```

- Sandbox tests need Docker and are skipped without it.
- Browser tests need `pip install playwright` and an installed Chrome or Edge; they are
  skipped if neither is there.
- CI runs everything on each push, with the sandbox built and browsers required.

## How the code is laid out

[CLAUDE.md](CLAUDE.md) is the map: what each file does, how a message travels, the
naming traps, and why the code is the way it is. Read it before a larger change.

A few rules the code depends on:

- **Rimuru (the router) never does the work.** It has no shell and no file tools, on
  purpose. Work goes to a clone.
- **Everything durable is in SQLite.** A loop can die at any moment and resume.
- **Nothing a clone runs touches the computer directly.** Commands go through the Docker
  sandbox; the host shell only with an explicit opt-in.
- **No key reaches a model.** Anything sent to a model, a reply, a pull request or the
  log passes through `safety.scrubbed()`.
- **The pages have no build step.** Plain ES modules and one CSS file per feature.
- **Plain words on the pages.** No internal IDs, no jargon on the chat page; the technical
  detail goes in Under the hood.

## A pull request

- One change per pull request, with a test that fails without it.
- New text on the pages gets its French and Arabic lines in `static/js/i18n.js`.
- Say what you checked by hand, in a browser or with a real model.
- By sending a pull request you agree that your work is shared under the
  [GNU AGPL-3.0](LICENSE), the project's licence.
