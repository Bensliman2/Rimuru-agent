"""The session notebook: clones working on one session take each other's findings
into account, cannot contradict each other unseen, and a disagreement still open
is kept in the final report instead of being flattened away. Offline."""
from __future__ import annotations

import asyncio
from pathlib import Path

from rimuru import notebook
from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.models import Task
from rimuru.recall import recall
from rimuru.system import RimuruSystem


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    config.max_workers = 0          # the clones wait: these tests drive them by hand
    return RimuruSystem(config, speed=50.0)


async def clone(system, title, key="s-note0001", **task):
    thread = system.store.get_or_create_thread("console", key, "A session")
    made = system.store.put_task(Task(id=new_id("task"), title=title, brief="the brief", thread_id=thread.id, **task))
    session, _ = await system.pool.submit(made, thread_id=thread.id)
    return thread, system.pool.workers[session.id]


async def note(worker, **args):
    return await worker._tool_note_finding({"relation": "new", **args}, worker.loop)


def test_subjects_that_name_the_same_thing():
    assert notebook.same_subject("parse_date", "the parse_date function")
    assert notebook.same_subject("Login page", "login  PAGE")
    assert not notebook.same_subject("login page", "login API")
    assert not notebook.same_subject("parse_date", "the database")
    assert notebook.same_subject("convert_to_utc.py offsets", "convert_to_utc.py offset")


def test_a_finding_reaches_the_other_clones_now_and_later(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread, a = await clone(system, "Find the bug")
        _, b = await clone(system, "Write the tests")
        said = await note(a, subject="parse_date", claim="It drops the time zone.",
                          evidence="test_dates.py::test_tz fails")
        assert said.startswith("Noted as [fnd_")
        assert "drops the time zone" in b.loop._notes[-1], "a clone at work hears of it"
        assert not a.loop._notes, "not the clone that wrote it"
        # A clone started later reads it in its brief; read_notebook shows it all.
        _, c = await clone(system, "Fix the bug")
        brief = c.loop.inbox._queue[0].text
        assert "THE SESSION NOTEBOOK" in brief and "drops the time zone" in brief
        assert "test_tz fails" in await c._tool_read_notebook({}, c.loop)
        # Another session's notebook is its own.
        _, other = await clone(system, "Something else", key="s-note0002")
        assert "THE SESSION NOTEBOOK" not in other.loop.inbox._queue[0].text
        await system.stop()

    asyncio.run(run())


def test_a_clone_cannot_contradict_another_silently(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _, a = await clone(system, "Find the bug")
        _, b = await clone(system, "Check the dates")
        await note(a, subject="parse_date", claim="It drops the time zone.")
        first = next(iter(system.store.findings.values()))

        # Same subject, said as if new: refused, and shown what is there.
        refused = await note(b, subject="the parse_date function", claim="The time zone is kept.")
        assert "already has" in refused and first.id in refused and len(system.store.findings) == 1

        said = await note(b, subject="parse_date", claim="The time zone is kept.", relation="contradicts")
        assert "contradicts" in said and len(system.store.findings) == 2
        assert "CONTRADICTS your finding" in a.loop._notes[-1], "the contradicted clone is told"
        assert "Clones disagree" in system.router.loop._notes[-1], "and so is Rimuru"
        pairs = notebook.disagreements(system.store, a.scope)
        assert len(pairs) == 1 and pairs[0][0].id == first.id
        assert "OPEN DISAGREEMENTS" in notebook.for_brief(system.store, a.scope)
        await system.stop()

    asyncio.run(run())


def test_settling_needs_evidence_and_closes_the_disagreement(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _, a = await clone(system, "Find the bug")
        _, b = await clone(system, "Check the dates")
        await note(a, subject="parse_date", claim="It drops the time zone.")
        await note(b, subject="parse_date", claim="The time zone is kept.", relation="contradicts")
        theirs = [f for f in system.store.findings.values() if f.worker_id == b.session.id][0]

        refused = await note(a, subject="parse_date", claim="It does drop it.", relation="settles", other=theirs.id)
        assert "needs evidence" in refused
        said = await note(a, subject="parse_date", claim="It drops it for offsets other than UTC.",
                          relation="settles", other=theirs.id, evidence="ran test_tz with +02:00: fails")
        assert "settled" in said
        assert not notebook.disagreements(system.store, a.scope)
        assert [f.claim for f in notebook.open_findings(system.store, a.scope)] == [
            "It drops it for offsets other than UTC."]
        assert "Settled" in system.router.loop._notes[-1]
        await system.stop()

    asyncio.run(run())


def test_settling_closes_the_contradiction_even_under_another_subject(tmp_path):
    """Seen on a real model: the clone contradicted under one subject, then settled
    under a slightly different one; its contradiction stayed open."""
    async def run():
        system = make_system(tmp_path)
        _, a = await clone(system, "Find the bug")
        _, b = await clone(system, "Check the dates")
        await note(a, subject="convert_to_utc.py offsets", claim="It ignores the offset.")
        first = next(iter(system.store.findings.values()))
        await note(b, subject="convert_to_utc.py offset handling", claim="The offset is applied.",
                   relation="contradicts", other=first.id)
        await note(b, subject="convert_to_utc.py offsets", claim="14:00 +02:00 gives 12:00 UTC.",
                   relation="settles", other=first.id, evidence="ran it: 12:00:00+00:00")
        assert [f.claim for f in notebook.open_findings(system.store, a.scope)] == ["14:00 +02:00 gives 12:00 UTC."]
        await system.stop()

    asyncio.run(run())


def test_changing_your_own_mind_replaces_your_finding(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _, a = await clone(system, "Find the bug")
        await note(a, subject="parse_date", claim="It drops the time zone.")
        await note(a, subject="parse_date", claim="Actually it is the locale.")
        found = notebook.open_findings(system.store, a.scope)
        assert [f.claim for f in found] == ["Actually it is the locale."]
        assert sorted(f.status for f in system.store.findings.values()) == ["open", "replaced"]
        await system.stop()

    asyncio.run(run())


def test_an_open_disagreement_is_kept_in_the_last_words(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _, a = await clone(system, "Find the bug")
        _, b = await clone(system, "Check the dates")
        await note(a, subject="parse_date", claim="It drops the time zone.")
        await note(b, subject="parse_date", claim="The time zone is kept.", relation="contradicts")

        # The first finish is held once, with both sides, so the summary keeps them.
        held = await b._tool_finish({"summary": "Done.", "outcome": "done", "for_person": "Ready."}, b.loop)
        assert held.startswith("Not finished yet") and "drops the time zone" in held
        assert "for_person" in held, "a chat job says what is not sure, in plain words"
        assert system.store.tasks[b.task.id].status != "done"

        await b._tool_finish({"summary": "Kept both sides.", "outcome": "done",
                              "for_person": "Ready, but one point is not sure yet."}, b.loop)
        assert system.store.tasks[b.task.id].status == "done"
        told = system.router.loop._notes[-1]
        assert "Still open in this session's notebook" in told and "both sides" in told
        assert "drops the time zone" in told and "The time zone is kept." in told
        await system.stop()

    asyncio.run(run())


def test_findings_survive_a_restart_are_recalled_and_leave_with_their_session(tmp_path):
    async def run():
        system = make_system(tmp_path)
        thread, a = await clone(system, "Find the bug")
        await note(a, subject="parse_date", claim="It drops the time zone.", evidence="test_tz")
        fid = next(iter(system.store.findings))
        await system.stop()
        system.store.close()

        again = make_system(tmp_path)
        assert again.store.findings[fid].claim == "It drops the time zone."
        assert "drops the time zone" in recall(fid, again.store)
        again.store.delete_session_records(thread.id)
        assert not again.store.findings
        await again.stop()

    asyncio.run(run())


def test_no_key_goes_into_the_notebook(tmp_path):
    async def run():
        system = make_system(tmp_path)
        _, a = await clone(system, "Set up the API")
        said = await note(a, subject="API", claim="the api key is sk-abcdefghijklmnopqrstuvwxyz123456")
        assert "Refused" in said and not system.store.findings
        await system.stop()

    asyncio.run(run())
