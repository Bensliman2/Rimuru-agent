"""Ten everyday requests, run for real and graded by code (docs/production-plan.md, phase 1).

    python run.py golden                       against http://127.0.0.1:8800
    python run.py golden --url http://127.0.0.1:8802 --only 1,7

Each request goes in its own session of a running Rimuru with a real model. A
job passes when it finished, the person was told, and what it made opens: its
page in the viewer, or the address a preview gave, answering with the words the
request was about. Results go to data/golden/<time>.json.

It uses real AI requests - about 5 to 10 a job - so run it before a release,
not on every change.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

JOBS = [
    ("Make a simple to-do list page where I can add tasks and tick them off.", r"to-?do|task"),
    ("Build a tip calculator page: the bill, the tip percent, and how much each person pays.", r"tip"),
    ("Make a one-page website for my bakery called Sweet Crumbs, with opening hours and a contact section.",
     r"sweet crumbs"),
    ("Create a countdown timer page where I set the minutes and press start.", r"start"),
    ("Make a page that converts kilometres to miles and back.", r"mile"),
    ("Build a small quiz page with three questions about the solar system and a score at the end.", r"score"),
    ("Crée une page web simple pour mon café « Le Petit Matin », avec le menu et les prix.", r"petit matin"),
    ("اصنع صفحة ويب بسيطة لمحل ورد اسمه «ياسمين» فيها قائمة الأسعار.", r"ياسمين"),
    ("Make a page that checks how strong a password is while I type it.", r"strong|strength|weak"),
    ("Build a tiny Flask app with one page that shows today's date, and run it so I can open it.",
     r"\d{4}|date|today"),
]
LOCAL_LINK = re.compile(r"https?://(?:127\.0\.0\.1|localhost):\d+/[^\s)\"'<]*")


def run(base: str, only: set[int] | None, minutes: float) -> int:
    client = httpx.Client(base_url=base, timeout=30)
    overview = client.get("/api/overview").json()
    if overview.get("demo"):
        print("This Rimuru is in demo mode: golden jobs need a real model.")
        return 2
    print(f"models: {overview.get('models_note')}\nshell: {overview['shell']['detail']}\n")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    results = []
    for number, (request, expected) in enumerate(JOBS, 1):
        if only and number not in only:
            continue
        key = f"s-gold{stamp[-4:]}{number:02d}"
        used_before = sum(q["used"] for q in client.get("/api/overview").json().get("quota") or [])
        started = time.time()
        client.post("/api/message", json={"text": request, "thread_key": key})
        result = {"job": number, "request": request, "key": key, "finished": False, "told": False,
                  "opens": False, "how": "", "seconds": 0.0, "note": ""}
        while time.time() - started < minutes * 60:
            chat = client.get("/api/chat", params={"chat": key}).json()["messages"]
            ends = [m for m in chat if m["role"] == "event" and m.get("kind") in {"done", "failed", "cancelled"}]
            working = client.get("/api/work", params={"chat": key}).json()
            if ends and not any(c["phase"] in {"working", "waiting"} for c in working["cards"]):
                break
            time.sleep(4)
        result["seconds"] = round(time.time() - started, 1)
        chat = client.get("/api/chat", params={"chat": key}).json()["messages"]
        work = client.get("/api/work", params={"chat": key}).json()
        result["finished"] = any(m["role"] == "event" and m.get("kind") == "done" for m in chat)
        said = [m for m in chat if m["role"] == "rimuru" and m["ts"] > started]
        result["told"] = bool(said)
        words = re.compile(expected, re.IGNORECASE)
        port = work.get("view_port") or 0
        candidates = []
        for card in work["cards"]:
            if card.get("view") and port:
                candidates.append(("page", f"http://127.0.0.1:{port}/{key}/{card['view']}"))
        if work["folder"].get("view") and port:
            candidates.append(("page", f"http://127.0.0.1:{port}/{key}/{work['folder']['view']}"))
        for message in said:
            candidates += [("preview", link) for link in LOCAL_LINK.findall(message.get("text") or "")
                           if f":{base.rsplit(':', 1)[-1]}/" not in link]
        for how, link in candidates:
            try:
                answer = httpx.get(link, timeout=10, follow_redirects=True)
            except httpx.HTTPError as exc:
                result["note"] = f"{link}: {type(exc).__name__}"
                continue
            if answer.status_code == 200 and words.search(answer.text):
                result["opens"], result["how"] = True, how
                break
            result["note"] = f"{link}: HTTP {answer.status_code}" + ("" if answer.status_code != 200
                                                                    else ", without the expected words")
        if not candidates:
            result["note"] = "nothing to open"
        used_after = sum(q["used"] for q in client.get("/api/overview").json().get("quota") or [])
        result["requests"] = max(0, used_after - used_before)
        results.append(result)
        mark = "PASS" if result["finished"] and result["told"] and result["opens"] else "FAIL"
        print(f"{mark} {number:2d}  {result['seconds']:6.0f}s  {result['requests']:3d} requests  "
              f"{'opens (' + result['how'] + ')' if result['opens'] else result['note']}  {request[:60]}",
              flush=True)
    passed = sum(1 for r in results if r["finished"] and r["told"] and r["opens"])
    print(f"\n{passed} of {len(results)} golden jobs end with something that opens.")
    out = Path("data") / "golden" / f"{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"base": base, "passed": passed, "total": len(results), "results": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"details: {out}")
    return 0 if passed >= max(1, round(len(results) * 0.8)) else 1


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python run.py golden")
    parser.add_argument("--url", default="http://127.0.0.1:8800")
    parser.add_argument("--only", default="", help="job numbers, e.g. 1,7")
    parser.add_argument("--minutes", type=float, default=8.0, help="the longest one job may take")
    args = parser.parse_args(argv)
    only = {int(n) for n in args.only.split(",") if n.strip()} or None
    return run(args.url.rstrip("/"), only, args.minutes)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
