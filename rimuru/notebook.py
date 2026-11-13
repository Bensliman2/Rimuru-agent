"""The session notebook: what the clones working in one session found out, shared.

Three things go wrong when several agents work on parts of one problem:
  - One ignores what another found. So every finding is written here, with how
    it is known; a clone's brief lists them, and new ones reach the clones at work.
  - Two contradict each other without anyone seeing it. So a clone cannot add a
    finding on a subject another clone already wrote about without saying whether
    it confirms, contradicts or settles it. A contradiction is told to Rimuru and
    to the other clone, and never settled by a vote or an opinion: settling needs
    evidence from a check (a test, a command, the code).
  - The final report flattens the work. So findings stay whole and reachable by ID
    (recall(fnd_...)), and a disagreement still open must be in the clone's last
    words and reaches Rimuru with the result: both sides, not one picked by guessing.

A notebook belongs to a thread: a chat session, a GitHub issue, a team chat.
"""
from __future__ import annotations

from typing import Any

from . import persona
from .ids import new_id
from .memory import _words, looks_secret, same
from .models import Finding

RELATIONS = ("new", "confirms", "contradicts", "settles")
SHOWN = 25                       # findings in a brief: the newest ones
SHOWN_CHARS = 3500


def same_subject(a: str, b: str) -> bool:
    """Two subjects name the same thing: the same words, or one inside the other
    ("parse_date" and "the parse_date function"; not "login page" and "login API")."""
    if same(a, b):
        return True
    # "offset" and "offsets": a plural is the same subject.
    wa, wb = ({w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _words(t)} for t in (a, b))
    if not wa or not wb:
        return False
    shared = len(wa & wb)
    return shared >= min(len(wa), len(wb)) and shared / max(len(wa), len(wb)) >= 0.5


def open_findings(store: Any, scope: str) -> list[Finding]:
    return [f for f in store.findings_of(scope) if f.status == "open"]


def on_subject(store: Any, scope: str, subject: str, *, not_by: str = "") -> list[Finding]:
    return [f for f in open_findings(store, scope)
            if f.worker_id != not_by and same_subject(f.subject, subject)]


def disagreements(store: Any, scope: str, *, worker_id: str = "") -> list[tuple[Finding, Finding]]:
    """Open contradictions: (the finding, the one contradicting it). With worker_id,
    only those where that clone wrote one side."""
    out = []
    for f in open_findings(store, scope):
        if f.relation != "contradicts":
            continue
        other = store.findings.get(f.other)
        if other is None or other.status != "open":
            continue
        if worker_id and worker_id not in {f.worker_id, other.worker_id}:
            continue
        out.append((other, f))
    return out


def author(store: Any, finding: Finding) -> str:
    clone = store.sessions.get(finding.worker_id)
    return persona.of(clone)[0] if clone is not None else "a clone"


def line(store: Any, f: Finding) -> str:
    how = f" (how it is known: {f.evidence})" if f.evidence else " (not checked)"
    tag = {"confirms": f", confirms [{f.other}]", "contradicts": f", CONTRADICTS [{f.other}]",
           "settles": f", settles [{f.other}]"}.get(f.relation, "")
    return f"  [{f.id}] {f.subject}: {f.claim}{how} - {author(store, f)}{tag}"


def pair_lines(store: Any, pairs: list[tuple[Finding, Finding]]) -> str:
    return "\n".join(f"  - on {a.subject}: {author(store, a)} says \"{a.claim}\" [{a.id}], "
                     f"{author(store, b)} says \"{b.claim}\" [{b.id}]" for a, b in pairs)


def for_brief(store: Any, scope: str, *, everything: bool = False) -> str:
    """The notebook as a clone reads it: its open findings, then its open disagreements."""
    if not scope:
        return ""
    found = open_findings(store, scope)
    if not found:
        return ""
    shown, used = [], 0
    for f in reversed(found if everything else found[-SHOWN:]):
        text = line(store, f)
        if used + len(text) > SHOWN_CHARS and not everything:
            break
        shown.append(text)
        used += len(text)
    out = ("THE SESSION NOTEBOOK - what clones in this session found out. Take it into account; "
           "add what you find with note_finding:\n" + "\n".join(reversed(shown)) + "\n")
    if len(shown) < len(found):
        out += f"  ({len(found) - len(shown)} older ones: read_notebook shows them all)\n"
    pairs = disagreements(store, scope)
    if pairs:
        out += ("OPEN DISAGREEMENTS - settle one only with a check, or say plainly that it is open:\n"
                + pair_lines(store, pairs) + "\n")
    return out + "\n"


def add(store: Any, *, scope: str, worker_id: str, task_id: str, subject: str, claim: str,
        evidence: str = "", relation: str = "new", other: str = "") -> tuple[Finding | None, str, Finding | None]:
    """Write a finding. Returns (the finding or None if refused, what to tell the clone,
    the finding it confirms, contradicts or settles)."""
    subject, claim, evidence = (" ".join(str(x or "").split()) for x in (subject, claim, evidence))
    other = str(other or "").strip()
    relation = relation if relation in RELATIONS else "new"
    if not scope:
        return None, "This job has no session, so there is no notebook to write in.", None
    if not subject or not claim:
        return None, "Give both a subject (what it is about) and the claim (what you found).", None
    if looks_secret(f"{claim} {evidence}"):
        return None, "Refused: that looks like a key or a password. Never write one in the notebook.", None
    if len(claim) > 600 or len(evidence) > 600:
        return None, "Too long: one fact, in a sentence or two (and the evidence in one line).", None

    others = on_subject(store, scope, subject, not_by=worker_id)
    target: Finding | None = None
    if relation == "new":
        if others:
            return None, ("The notebook already has, on this subject, from another clone:\n"
                          + "\n".join(line(store, f) for f in others)
                          + "\nSay how yours relates: relation=confirms, contradicts or settles, with "
                          "other=<its id>. If yours is about something else, give a more precise subject."), None
    else:
        target = store.findings.get(other) if other else (others[0] if len(others) == 1 else None)
        if target is None or target.scope != scope or target.status != "open":
            listed = "\n".join(line(store, f) for f in others) or "  (nothing open on this subject)"
            return None, ("other must be the ID of an open finding in this session's notebook. "
                          f"On this subject:\n{listed}"), None
        if relation == "settles" and not evidence:
            return None, ("Settling needs evidence: the test, the command or the code that shows it. "
                          "Without one, note it as contradicts and leave it open."), None
        if target.worker_id == worker_id and relation != "settles":
            relation, target = "new", None      # changing your own mind replaces your finding

    finding = Finding(id=new_id("finding"), scope=scope, subject=subject[:160], claim=claim,
                      evidence=evidence, task_id=task_id, worker_id=worker_id, relation=relation,
                      other=target.id if target is not None else "")
    for f in open_findings(store, scope):
        mine = f.worker_id == worker_id and same_subject(f.subject, subject)
        settled = relation == "settles" and target is not None and (
            same_subject(f.subject, subject) or f is target or f.other == target.id)
        if mine or settled:
            f.status, f.closed_by = ("settled" if settled and not mine else "replaced"), finding.id
            store.put_finding(f)
    store.put_finding(finding)

    if relation == "contradicts":
        said = (f"Noted as [{finding.id}]. It contradicts [{target.id}] by {author(store, target)}: "
                "Rimuru and that clone are told. Settle it with a check if you can (note_finding with "
                "relation=settles and the evidence); otherwise keep both sides in your finish summary.")
    elif relation == "settles":
        said = f"Noted as [{finding.id}]: this subject is settled, and the earlier findings on it are closed."
    elif relation == "confirms":
        said = f"Noted as [{finding.id}], confirming [{target.id}]."
    else:
        said = f"Noted as [{finding.id}]. The other clones in this session see it."
    return finding, said, target


def for_router(store: Any, pairs: list[tuple[Finding, Finding]]) -> str:
    return ("Still open in this session's notebook - clones disagree:\n" + pair_lines(store, pairs)
            + "\nWhen you tell the person about this work, give both sides in plain words and say "
            "it is not settled. Do not pick one by guessing; a new job can check it.")
