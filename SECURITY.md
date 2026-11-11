# Security

Rimuru runs AI models that write and run code on your computer. This page says
what protects you, what does not, and how to report a problem.

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's
**Report a vulnerability** button on this repository (Security tab), with what
you did, what happened, and the version (`python run.py --version`). You will get
an answer within a week.

## What Rimuru is built to protect

| Asset | Threat | What protects it |
|---|---|---|
| Your files | A clone runs a harmful command, written by the model or by text planted in a file it read | Every command runs in a throwaway Docker container: only the job's folder mounted, no network, read-only system, all capabilities dropped, memory, CPU and process limits. `RIMURU_SANDBOX_RUNTIME=runsc` adds gVisor. |
| Your API keys | They leak to an AI service, a chat, a pull request or a log | Keys live only in `.env`. Clones never open files that hold keys (`.env`, `id_rsa`, `*.pem`...): inside the sandbox those files are blanked. Everything sent to a model, every reply, every pull request and the log pass through a filter that hides keys. |
| The app's controls | Another website or a page a clone built drives the app | The app answers only this computer, refuses other host names (DNS rebinding) and cross-site requests. Built pages open on a separate port, so they cannot reach the app. |
| Your work | The model is steered by hidden instructions in a file or message | Clones treat what they read as content. Instructions aimed at AI tools, in English, French or Arabic, are reported to you in the chat. |
| Who may give work | Strangers on Telegram or GitHub | Only people you allow. Everyone else gets one polite refusal. |
| Remote access | Someone else opens your Rimuru | It answers only this computer unless you choose otherwise. Then set `RIMURU_PASSWORD`, and put it behind HTTPS (see the README). |

## What it does not protect

- **A model you do not trust.** Clones follow the model. A model that decides to
  delete everything in the job's folder can. Pick a session folder for the work,
  not one full of personal files, and keep backups.
- **Previews.** A running app started with the `preview` tool can reach the
  internet (an app may need an API). It still sees only its own folder, with keys
  blanked, and stops after an hour.
- **Package installs.** `install` downloads from PyPI or npm in a separate
  container that sees only the package folder; Python takes ready-made wheels
  only and npm runs no install scripts. A malicious package can still run when
  the clone's own code imports it, inside the sandbox.
- **Free AI services** may log what you send. Send nothing confidential through them.
- **Host mode** (`RIMURU_SHELL=host`) runs commands directly on your computer,
  with no sandbox. It is off unless you turn it on.
- **Experimental features**: GitHub and remote hands were tested with simulated
  services and on one computer, not yet against real services and machines.

## Checking

`python -m pytest tests/test_sandbox.py tests/test_secrets.py tests/test_safety.py`
runs the checks behind this page, with real containers when Docker is running.
