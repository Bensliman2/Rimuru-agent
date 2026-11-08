"""Does the Docker setup really work? Run it inside the running stack:

    docker compose exec -T -u "$(id -u):$(id -g)" -e HOME=/tmp rimuru python scenarios/docker_smoke.py

A clone's command must run in a sealed container of the sandbox service - seeing
only its folder, with no internet and no keys - and what it writes must land in
rimuru-data/, belonging to the person.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rimuru.worker.shell import DockerShell  # noqa: E402

DATA = Path("/rimuru/data")
FOLDER = DATA / "sessions" / "docker-smoke"
checks: list[tuple[str, bool, str]] = []


def check(what: str, passed: bool, seen: str = "") -> None:
    checks.append((what, passed, seen))
    print(f"{'ok  ' if passed else 'FAIL'} {what}" + ("" if passed else f"\n     saw: {seen[:600]}"), flush=True)


async def main() -> int:
    shutil.rmtree(FOLDER, ignore_errors=True)
    FOLDER.mkdir(parents=True)
    (FOLDER / ".env").write_text("TOKEN=super-secret-value\n", encoding="utf-8")

    check("the app does not run as root", os.getuid() != 0, str(os.getuid()))
    check("the sandbox service answers", DockerShell.available())
    shell = DockerShell(FOLDER, blank_dir=DATA)
    shell.prepare_in_background()
    waited = time.time()
    while not shell.ready and not shell.error and time.time() - waited < 600:
        await asyncio.sleep(2)
    check("the clones' sandbox image is ready", shell.ready, shell.error)
    if not shell.ready:
        return 1

    status, out = await shell.run("echo made > made.txt && python -c 'print(6 * 7)'")
    check("a command runs", status == "done" and "42" in out, out)
    made = FOLDER / "made.txt"
    check("what it writes lands in the session's folder", made.is_file(), out)
    if made.is_file():
        check("and belongs to the person", made.stat().st_uid == os.getuid(), str(made.stat().st_uid))

    status, out = await shell.run("hostname; id -u")
    check("in a container of its own", socket.gethostname() not in out.split(), out)

    status, out = await shell.run("cat .env; echo end")
    check("files that hold keys are empty inside", "super-secret-value" not in out and "end" in out, out)

    status, out = await shell.run("ls /rimuru/data 2>&1; ls /certs 2>&1; true")
    check("only its own folder is visible", "rimuru.db" not in out and "ca.pem" not in out, out)

    status, out = await shell.run(
        "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\" 2>&1 || echo offline")
    check("no internet", "offline" in out, out)

    shutil.rmtree(FOLDER, ignore_errors=True)
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)} of {len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
