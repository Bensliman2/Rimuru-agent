"""Shared test setup."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _own_skills_folder(tmp_path, monkeypatch):
    """Every test gets an empty skills folder of its own - never the person's
    real skills/ (rimuru/skills.py)."""
    from rimuru.config import CONFIG

    folder = tmp_path / "skills-of-this-test"
    monkeypatch.setenv("RIMURU_SKILLS", str(folder))
    monkeypatch.setattr(CONFIG, "skills_dir", folder)
    yield
