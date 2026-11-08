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


@pytest.fixture
def steady_demo(monkeypatch):
    """The offline stand-in fails 12% of pretend commands on purpose, and now and
    then a clone "drifts" until the watchdog nudges it. Real behaviour for the
    demo, and noise for a test that needs every job to finish."""
    import random

    from rimuru.llm import mock

    original = mock.MockClient.__init__

    def steady(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.drift = False

    monkeypatch.setattr(random, "random", lambda: 0.99)
    monkeypatch.setattr(mock.MockClient, "__init__", steady)
