"""GitHub: Rimuru answers on issues and pull requests, and opens draft pull requests.

Polls the repositories you list (every minute, from now on - never their whole
history) for issues and comments that mention the calling word (@rimuru) - from
the people you allow only. It answers in the thread, signed as an AI assistant.

Work on a thread happens in a copy of the repository on this computer, on a
branch of its own (rimuru/issue-N). When the clone is done, Rimuru commits,
pushes that branch and opens a DRAFT pull request, and links it in the thread.
It never merges.

Safety: the copy's git folder is kept OUTSIDE the folder the clone works in, and
every git command names it explicitly. A clone (or a hostile repository) can
write anything into its folder - a .git file, .gitattributes - but can never
reach the git configuration or hooks that git on this computer runs. The token
is passed to git through the environment, never written into any config.

Its channel is "gh": the practice runs' simulated "github" can never post here.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ..ids import new_id
from ..models import Trigger
from ..safety import has_secret, scrubbed, secret_file
from .base import ASYNC_NORMS
from ..logs import noted

log = logging.getLogger("rimuru.github")

SIGNATURE = "— Rimuru, an AI assistant"
KEY_RE = re.compile(r"^(?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9._-]+)#(?P<n>\d+)$")


class Problem(RuntimeError):
    pass


class GitHubChannel:
    name = "gh"
    norms = ASYNC_NORMS

    def __init__(self, *, token: str, settings: Any, system: Any,
                 http: httpx.AsyncClient | None = None, api: str = "https://api.github.com",
                 remote: str = "https://github.com/{repo}.git", interval: float = 60.0) -> None:
        self._token = token
        self.settings = settings
        self.system = system
        self.api = api.rstrip("/")
        self.remote = remote
        self.interval = interval
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0))
        self.root = Path(system.config.db_path).resolve().parent / "github"
        self.task: asyncio.Task | None = None
        self.state = "starting"
        self.problem = ""
        self.login = ""
        self.seen: set[str] = set()
        self._titles: dict[str, str] = {}
        self._default: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # the API
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "rimuru"}

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.http.request(method, f"{self.api}{path}", headers=self._headers(),
                                               **kwargs)
        except httpx.HTTPError as exc:
            raise Problem(f"GitHub could not be reached ({type(exc).__name__}).") from None
        if response.status_code == 401:
            raise Problem("GitHub did not accept the token.")
        if response.status_code >= 400:
            try:
                message = response.json().get("message", "")
            except ValueError:
                message = response.text[:120]
            raise Problem(f"GitHub said ({response.status_code}): {str(message)[:200]}")
        return response.json() if response.content else None

    async def check(self) -> str:
        me = await self.call("GET", "/user")
        self.login = str(me.get("login") or "")
        return self.login

    def allowed(self, login: str) -> bool:
        people = self.settings.gh["allowed"] or ([self.login] if self.login else [])
        return login.lower() in {p.lower() for p in people}

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._poll(), name="github")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        with noted("closing the GitHub connection", logging.DEBUG):
            await self.http.aclose()

    async def _poll(self) -> None:
        while True:
            try:
                if not self.login:
                    await self.check()
                for repo in list(self.settings.gh["repos"]):
                    await self.poll_repo(repo)
                self.state, self.problem = "watching", ""
            except asyncio.CancelledError:
                raise
            except Problem as exc:
                self.state, self.problem = "problem", str(exc)
                if "did not accept" in str(exc):
                    return                          # waits for a new token (Connections)
            except Exception as exc:  # noqa: BLE001
                log.warning("checking GitHub failed", exc_info=True)
                self.state, self.problem = "problem", type(exc).__name__
            await asyncio.sleep(self.interval)

    # ------------------------------------------------------------------ #
    # reading
    # ------------------------------------------------------------------ #
    def _mentions(self, body: str) -> bool:
        word = re.escape(self.settings.gh["mention"].lstrip("@"))
        return bool(re.search(rf"(?<![\w-])@{word}\b", body or "", re.IGNORECASE))

    async def poll_repo(self, repo: str) -> int:
        """Read what is new on one repository since last time. Returns how many
        things reached Rimuru."""
        since = self.settings.gh["checked"].get(repo) or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
        params = {"since": since, "sort": "created", "direction": "asc", "per_page": 100}
        found = 0
        issues = await self.call("GET", f"/repos/{repo}/issues", params={**params, "state": "open",
                                                                          "per_page": 50}) or []
        for issue in issues:
            if issue.get("created_at", "") < since:
                continue                              # updated, not new
            n = issue["number"]
            self._titles[f"{repo}#{n}"] = issue.get("title") or ""
            found += await self._consider(repo, n, f"issue-{issue['id']}", issue.get("user") or {},
                                          f"{issue.get('title') or ''}\n\n{issue.get('body') or ''}",
                                          issue.get("html_url", ""))
        for comment in await self.call("GET", f"/repos/{repo}/issues/comments", params=params) or []:
            n = int(str(comment.get("issue_url", "")).rsplit("/", 1)[-1] or 0)
            found += await self._consider(repo, n, f"comment-{comment['id']}", comment.get("user") or {},
                                          comment.get("body") or "", comment.get("html_url", ""))
        for comment in await self.call("GET", f"/repos/{repo}/pulls/comments", params=params) or []:
            n = int(str(comment.get("pull_request_url", "")).rsplit("/", 1)[-1] or 0)
            where = f"(on {comment.get('path', 'a file')}, line {comment.get('line') or '?'}) "
            found += await self._consider(repo, n, f"review-{comment['id']}", comment.get("user") or {},
                                          where + (comment.get("body") or ""), comment.get("html_url", ""))
        self.settings.gh["checked"][repo] = started
        self.settings.save()
        return found

    async def _consider(self, repo: str, number: int, seen: str, user: dict[str, Any],
                        body: str, url: str) -> int:
        if not number or seen in self.seen:
            return 0
        self.seen.add(seen)
        login = str(user.get("login") or "")
        if SIGNATURE in body or not self.allowed(login):
            return 0                                   # its own posts; people not allowed
        key = f"{repo}#{number}"
        known = self.system.store.thread_by_key.get(f"gh:{key}")
        mentioned = self._mentions(body)
        if not mentioned and not known:
            return 0                                   # not for Rimuru
        title = self._titles.get(key) or await self._title(repo, number)
        await self.system.ingest(Trigger(
            id=new_id("trigger"), channel="gh", thread_key=key, author=login,
            # A mention is someone talking to Rimuru directly; other comments on a
            # thread it already works on are follow-ups for the clone on it.
            kind="human_direct" if mentioned else "review", content=body.strip()[:6000],
            meta={"thread_title": f"{key} {title}".strip()[:100], "url": url}))
        return 1

    async def _title(self, repo: str, number: int) -> str:
        with contextlib.suppress(Problem):
            issue = await self.call("GET", f"/repos/{repo}/issues/{number}")
            self._titles[f"{repo}#{number}"] = str(issue.get("title") or "")
        return self._titles.get(f"{repo}#{number}", "")

    # ------------------------------------------------------------------ #
    # answering
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse(key: str) -> tuple[str, int]:
        match = KEY_RE.match(key)
        if not match:
            raise Problem(f'"{key}" is not a GitHub thread.')
        return match["repo"], int(match["n"])

    async def deliver(self, thread_key: str, text: str) -> str:
        repo, number = self.parse(thread_key)
        await self.call("POST", f"/repos/{repo}/issues/{number}/comments",
                        json={"body": f"{text.strip()}\n\n<sub>{SIGNATURE}</sub>"})
        return text

    # ------------------------------------------------------------------ #
    # working on the code
    # ------------------------------------------------------------------ #
    def _paths(self, repo: str, number: int) -> tuple[Path, Path]:
        base = self.root / repo.replace("/", "__")
        return base / f"issue-{number}", base / f"issue-{number}.git"

    @staticmethod
    def branch(number: int) -> str:
        return f"rimuru/issue-{number}"

    def _env(self) -> dict[str, str]:
        # The token goes to git through the environment, for this command only.
        basic = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
        return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"}

    async def _run_git(self, *args: str, auth: bool = False) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=self._env() if auth else {**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        out, _ = await asyncio.wait_for(proc.communicate(), 600)
        text = (out or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise Problem(f"git {args[0] if args[0] != '-c' else args[-1]} failed: "
                          f"{text.replace(self._token, '***')[-300:]}")
        return text

    async def git(self, folder: Path, gitdir: Path, *args: str, config: tuple[str, ...] = (),
                  auth: bool = False) -> str:
        """Git on this computer, always told where its folder is - never reading a
        .git a clone may have written into the work folder."""
        settings = [part for item in config for part in ("-c", item)]
        return await self._run_git(*settings, f"--git-dir={gitdir}", f"--work-tree={folder}",
                                   *args, auth=auth)

    async def prepare(self, thread: Any, task: Any) -> Path:
        """The folder a job on this thread works in: a copy of the repository on
        its own branch, made the first time."""
        repo, number = self.parse(thread.key)
        folder, gitdir = self._paths(repo, number)
        if not (gitdir / "HEAD").exists():
            folder.parent.mkdir(parents=True, exist_ok=True)
            if folder.exists() and any(folder.iterdir()):
                raise Problem(f"{folder} is not empty, so the repository cannot be copied there.")
            await self._run_git("clone", "--quiet", "--depth", "50", f"--separate-git-dir={gitdir}",
                                self.remote.format(repo=repo), str(folder), auth=True)
            with contextlib.suppress(OSError):
                (folder / ".git").unlink()             # git's own folder stays out of reach
            await self.git(folder, gitdir, "checkout", "--quiet", "-B", self.branch(number))
        return folder

    async def publish(self, worker: Any, thread: Any, outcome: str, summary: str) -> str:
        """Commit the clone's work, push its branch, open a draft pull request."""
        repo, number = self.parse(thread.key)
        folder, gitdir = self._paths(repo, number)
        if not (gitdir / "HEAD").exists():
            return ""
        if not (await self.git(folder, gitdir, "status", "--porcelain")).strip():
            return "no code changed, so no pull request was opened"
        title = " ".join(str(worker.task.title).split())[:72]
        summary = scrubbed(summary)
        await self.git(folder, gitdir, "add", "-A")
        held = await self._hold_back_secrets(folder, gitdir)
        if not (await self.git(folder, gitdir, "diff", "--cached", "--name-only")).strip():
            return f"no pull request: the only changes hold keys, so they stayed here ({', '.join(held)})"
        await self.git(folder, gitdir, "commit", "--quiet", "-m",
                       f"{title}\n\n{summary.strip()[:2000]}\n\nFor #{number}. Made by Rimuru, an AI assistant.",
                       config=("user.name=Rimuru", "user.email=rimuru@users.noreply.github.com"))
        branch = self.branch(number)
        await self.git(folder, gitdir, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}",
                       auth=True)
        url, draft = await self._pull_request(repo, number, branch, title, summary)
        kept = f"; left out because they hold keys: {', '.join(held)}" if held else ""
        return f"pull request: {url}" + (" (a draft - nothing is merged)" if draft
                                         else " (ready for review - nothing is merged)") + kept

    async def _hold_back_secrets(self, folder: Path, gitdir: Path) -> list[str]:
        """Unstage every file that holds keys - by its name, or by what is in it.
        A pull request is public to everyone on the repository, and git never forgets."""
        staged = await self.git(folder, gitdir, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        held = []
        for rel in filter(None, staged.split("\0")):
            path = folder / rel
            try:
                text = path.read_text(encoding="utf-8", errors="replace") if path.stat().st_size < 2_000_000 else ""
            except OSError:
                text = ""
            if secret_file(rel) or has_secret(text):
                held.append(rel)
        if held:
            await self.git(folder, gitdir, "reset", "--quiet", "HEAD", "--", *held)
        return held

    async def _pull_request(self, repo: str, number: int, branch: str, title: str,
                            summary: str) -> tuple[str, bool]:
        owner = repo.split("/")[0]
        open_ones = await self.call("GET", f"/repos/{repo}/pulls",
                                    params={"head": f"{owner}:{branch}", "state": "open"}) or []
        if open_ones:
            return str(open_ones[0].get("html_url")), bool(open_ones[0].get("draft"))
        if repo not in self._default:
            info = await self.call("GET", f"/repos/{repo}")
            self._default[repo] = str(info.get("default_branch") or "main")
        body = f"{summary.strip()[:6000]}\n\nFor #{number}.\n\n<sub>{SIGNATURE}</sub>"
        new = {"title": title, "head": branch, "base": self._default[repo], "body": body}
        try:
            pr = await self.call("POST", f"/repos/{repo}/pulls", json={**new, "draft": True})
            return str(pr.get("html_url")), True
        except Problem as exc:
            if "draft" not in str(exc).lower():
                raise
            # Some plans have no draft pull requests: open a normal one - still never merged.
            pr = await self.call("POST", f"/repos/{repo}/pulls", json=new)
            return str(pr.get("html_url")), False
