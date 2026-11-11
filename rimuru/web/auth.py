"""An optional password, for running Rimuru on a small server and opening it
from a phone (RIMURU_PASSWORD).

Without it nothing changes: the app answers this computer only. With it, every
page, every API call, the live feed and the viewer ask for the password once,
then remember the browser for 30 days with a signed cookie that scripts cannot
read. Changing the password signs every browser out. Ten wrong tries from one
address in ten minutes, and that address waits.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import secrets
import time
from pathlib import Path
from typing import Any

COOKIE = "rimuru_session"
MAX_AGE = 30 * 24 * 3600
OPEN = ("/login", "/logout", "/api/health", "/static/")


class Auth:
    def __init__(self, config: Any) -> None:
        self.password = os.environ.get("RIMURU_PASSWORD", "")
        self._failures: dict[str, list[float]] = {}
        self._key = b""
        if self.password:
            secret_file = Path(config.db_path).parent / ".session_secret"
            if not secret_file.exists():
                secret_file.parent.mkdir(parents=True, exist_ok=True)
                secret_file.write_text(secrets.token_hex(32), encoding="utf-8")
                try:
                    os.chmod(secret_file, 0o600)
                except OSError:
                    pass
            secret = secret_file.read_text(encoding="utf-8").strip()
            # The password is part of the key: changing it signs everyone out.
            self._key = hashlib.sha256(f"{secret}:{self.password}".encode()).digest()

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def token(self) -> str:
        issued = str(int(time.time()))
        return f"{issued}.{hmac.new(self._key, issued.encode(), hashlib.sha256).hexdigest()}"

    def valid(self, cookie: str | None) -> bool:
        if not self.enabled:
            return True
        issued, _, signature = str(cookie or "").partition(".")
        if not issued.isdigit() or time.time() - int(issued) > MAX_AGE:
            return False
        expected = hmac.new(self._key, issued.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def open_path(self, path: str) -> bool:
        return any(path == p or (p.endswith("/") and path.startswith(p)) for p in OPEN)

    def check(self, given: str, caller: str) -> bool | None:
        """True or False - or None when this address has tried too often."""
        now = time.time()
        recent = [t for t in self._failures.get(caller, []) if now - t < 600]
        if len(recent) >= 10:
            self._failures[caller] = recent
            return None
        if hmac.compare_digest(given.encode(), self.password.encode()):
            self._failures.pop(caller, None)
            return True
        self._failures[caller] = [*recent, now]
        return False


def login_page(problem: str = "") -> str:
    note = f'<p class="bad" role="alert">{html.escape(problem)}</p>' if problem else ""
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Rimuru - sign in</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#F5F7FA;color:#0F1B2D;
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
form{{width:min(360px,calc(100% - 32px));padding:28px;border-radius:14px;background:#fff;
border:1px solid #E2E8F0;box-shadow:0 6px 20px rgba(15,27,45,.06)}}
h1{{font-size:20px;margin:0 0 6px}}p{{margin:0 0 16px;color:#5A6A7E}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:10px;border:1px solid #E2E8F0;font:inherit}}
button{{margin-top:12px;width:100%;padding:10px;border:0;border-radius:10px;background:#2380B8;color:#fff;
font:inherit;font-weight:650;cursor:pointer}}.bad{{color:#B42318}}
@media (prefers-color-scheme: dark){{body{{background:#0C1218;color:#E5EDF5}}form{{background:#121B24;border-color:#233140}}
input{{background:#17222D;color:#E5EDF5;border-color:#233140}}}}
</style>
<form method="post" action="/login"><h1>Rimuru</h1><p>Type the password to open it.</p>{note}
<input type="password" name="password" autocomplete="current-password" autofocus aria-label="Password">
<button type="submit">Open</button></form></html>"""
