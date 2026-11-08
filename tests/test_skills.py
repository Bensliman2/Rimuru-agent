"""Skills: one Markdown file each, listed in every clone's brief, read in full
with use_skill(), managed from the app's Skills panel. Offline."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from rimuru import skills
from rimuru.config import Config
from rimuru.ids import new_id
from rimuru.models import Step, Task
from rimuru.system import RimuruSystem
from rimuru.web import friendly


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    config.skills_dir = tmp_path / "skills"
    config.max_workers = 0          # the clones wait, so each brief stays unread in its inbox
    return RimuruSystem(config, speed=50.0)


# ---------------------------------------------------------------------- the files
def test_each_skill_is_its_own_file_and_hand_edits_count(tmp_path):
    folder = tmp_path / "skills"
    made = skills.save(folder, name="Our Python style", when="Writing Python code",
                       text="- Type hints everywhere\n- A test for every fix")
    path = folder / "our-python-style.md"
    assert made.slug == "our-python-style" and path.is_file()
    assert path.read_text(encoding="utf-8").startswith(
        "---\nname: Our Python style\nwhen: Writing Python code\nenabled: true\n---\n- Type hints")

    # Edited by hand: Rimuru sees the change.
    path.write_text(path.read_text(encoding="utf-8").replace("A test for every fix", "Two tests per fix"),
                    encoding="utf-8")
    assert "Two tests per fix" in skills.get(folder, "our-python-style").text

    # Made by hand: any file name, no header needed. The folder's README is not a skill.
    (folder / "Deploy Steps.md").write_text("Run ./deploy.sh on the server.", encoding="utf-8")
    (folder / "README.md").write_text("How skills work.", encoding="utf-8")
    assert [s.slug for s in skills.load_all(folder)] == ["Deploy Steps", "our-python-style"]
    hand = skills.get(folder, "deploy steps")
    assert (hand.name, hand.text, hand.enabled) == ("Deploy steps", "Run ./deploy.sh on the server.", True)

    # Renamed, it keeps its file; turned off, then deleted.
    renamed = skills.save(folder, slug="our-python-style", name="Python, our way", text="- Type hints")
    assert renamed.slug == "our-python-style" and skills.get(folder, "python, our way").slug == "our-python-style"
    skills.set_enabled(folder, "Deploy Steps", False)
    assert skills.get(folder, "Deploy Steps").enabled is False
    assert "enabled: false" in (folder / "Deploy Steps.md").read_text(encoding="utf-8")
    assert skills.delete(folder, "Deploy Steps") and not (folder / "Deploy Steps.md").exists()


def test_skills_refuse_secrets_twins_and_paths_outside_their_folder(tmp_path):
    folder = tmp_path / "skills"
    skills.save(folder, name="Deploy", text="ssh deploy@server and run ./deploy.sh")
    with pytest.raises(FileExistsError):
        skills.save(folder, name="deploy!", text="again")
    for bad in ({"name": "", "text": "x"}, {"name": "x", "text": "  "}, {"name": "x" * 61, "text": "x"},
                {"name": "Keys", "text": "use ghp_abcdefghijklmnopqrstuvwxyz0123456789"}):
        with pytest.raises(ValueError):
            skills.save(folder, **bad)
    # Talking about secrets is fine; a secret is not.
    assert skills.problem("Secrets", "", "Never commit passwords; keys live in .env.") is None

    (tmp_path / "outside.md").write_text("not a skill")
    assert skills.get(folder, "../outside") is None
    assert not skills.delete(folder, "../outside") and (tmp_path / "outside.md").exists()
    with pytest.raises(FileNotFoundError):
        skills.save(folder, slug="../outside", name="x", text="y")
    assert skills.slugify("Our  Python style!!") == "our-python-style" and skills.slugify("!!!") == "skill"


# ---------------------------------------------------------------------- the clones
def test_clones_see_the_list_and_read_a_skill_in_full(tmp_path):
    async def run():
        system = make_system(tmp_path)
        folder = system.config.skills_dir
        thread = system.store.get_or_create_thread("console", "s-skill001", "A page")

        async def new_clone():
            task = system.store.put_task(Task(id=new_id("task"), title="Build a page", brief="",
                                              thread_id=thread.id))
            session, _ = await system.pool.submit(task, thread_id=thread.id)
            worker = system.pool.workers[session.id]
            return worker, worker.loop.inbox.get_nowait().text

        worker, brief = await new_clone()
        assert "SKILLS" not in brief and "use_skill" not in worker.registry.names(), "no skills, no noise"

        skills.save(folder, name="Web pages", when="Building a web page",
                    text="- Check it at 375px wide\n- Labels on every input")
        skills.save(folder, name="Old habit", text="Use tables for layout.", enabled=False)
        worker, brief = await new_clone()
        assert "SKILLS" in brief and "- web-pages: Web pages - use when: Building a web page" in brief
        assert "375px" not in brief, "the brief lists skills; it does not paste them"
        assert "Old habit" not in brief and "use_skill" in worker.registry.names()

        full = await worker._tool_use_skill({"name": "Web Pages"}, worker.loop)
        assert full.startswith('SKILL "Web pages"') and "375px" in full and "Labels on every input" in full
        seen = [e for e in system.bus.history if e["topic"] == "skill.used"]
        assert seen and seen[-1]["skill"] == "web-pages" and seen[-1]["session_id"] == worker.session.id
        refused = await worker._tool_use_skill({"name": "Old habit"}, worker.loop)
        assert refused.startswith("No skill called") and "web-pages" in refused, "a skill that is off is off"

        # A clone's card and the plain-words timeline show which skills it read.
        system.store.add_step(Step(id=new_id("step"), session_id=worker.session.id, index=0,
                                   tool_calls=[{"id": "c1", "name": "use_skill", "args": {"name": "web-pages"}}]))
        card = friendly.worker_card(system.store, worker.session, system.config.conversation_channels)
        assert card["skills"] == ["web-pages"]
        assert friendly.describe_call("use_skill", {"name": "Web pages"}) == "Read your skill “Web pages”"
        await system.stop()

    asyncio.run(run())


def test_rimuru_hears_about_skills_only_when_they_come_up(tmp_path):
    system = make_system(tmp_path)
    skills.save(system.config.skills_dir, name="How we deploy", when="Putting the site online",
                text="Run ./deploy.sh")
    note = system.router._memories_for
    asked = note([SimpleNamespace(text="Which skills do you have?", kind="human")])
    assert "How we deploy" in asked and "Putting the site online" in asked
    assert "How we deploy" not in note([SimpleNamespace(text="Build me a page", kind="human")])
    # A clone's report that mentions skills is not the person asking.
    assert "How we deploy" not in note([SimpleNamespace(text="I used two skills", kind="event")])
    system.store.close()


# ---------------------------------------------------------------------- the app
def test_the_skills_panel_adds_changes_turns_off_and_deletes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rimuru.config import CONFIG
    from rimuru.web.app import create_app

    folder = tmp_path / "my-skills"
    for name, value in {"db_path": tmp_path / "data" / "t.db", "workspace": tmp_path / "ws",
                        "sessions_dir": tmp_path / "data" / "sessions", "models_path": tmp_path / "m.toml",
                        "skills_dir": folder, "demo": True, "setup": None}.items():
        monkeypatch.setattr(CONFIG, name, value)
    with TestClient(create_app(speed=50)) as client:
        assert client.get("/api/skills").json()["items"] == []
        added = client.post("/api/skills", json={"name": "How we test", "when": "Fixing a bug",
                                                  "text": "- A test that failed before the fix"}).json()
        assert added["skill"]["file"] == "how-we-test.md" and (folder / "how-we-test.md").is_file()
        assert added["items"][0]["used"] == 0
        twin = client.post("/api/skills", json={"name": "How we TEST", "text": "x"})
        assert twin.status_code == 409 and "already" in twin.json()["error"]
        secret = client.post("/api/skills", json={"name": "Keys", "text": "token ghp_abcdefghijklmnopqrstuvwxyz0123"})
        assert secret.status_code == 400 and "password or a key" in secret.json()["error"]

        off = client.put("/api/skills/how-we-test", json={"enabled": False}).json()
        assert off["items"][0]["enabled"] is False and "enabled: false" in (folder / "how-we-test.md").read_text()
        changed = client.put("/api/skills/how-we-test", json={"name": "Testing", "when": "Any change",
                                                               "text": "- Run every test", "enabled": True}).json()
        assert changed["skill"]["name"] == "Testing" and changed["items"][0]["file"] == "how-we-test.md"
        assert client.put("/api/skills/nope", json={"enabled": True}).status_code == 404

        assert client.delete("/api/skills/how-we-test").json()["items"] == []
        assert client.delete("/api/skills/how-we-test").status_code == 404
        assert not (folder / "how-we-test.md").exists()
