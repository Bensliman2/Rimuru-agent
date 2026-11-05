"""Hidden acceptance tests for the unit converter the agent is asked to build.

Like the calculator's, this file sits outside the agent's workspace, so the
agent cannot see what it is graded on.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(os.environ.get("RIMURU_WORKSPACE", "data/workspace")).resolve()
SOURCE = WORKSPACE / "units" / "converter.py"


@pytest.fixture(scope="module")
def convert():
    if not SOURCE.exists():
        elsewhere = sorted(WORKSPACE.rglob("converter.py"))
        hint = (f" - it wrote {elsewhere[0].relative_to(WORKSPACE)} instead"
                if elsewhere else "")
        pytest.fail(f"the agent did not create {SOURCE}{hint}")
    sys.path.insert(0, str(WORKSPACE))
    try:
        module = importlib.import_module("units.converter")
    except ImportError:
        sys.path.insert(0, str(WORKSPACE / "units"))
        module = importlib.import_module("converter")
    return module.convert


@pytest.mark.parametrize("value, src, dst, expected", [
    (1, "km", "m", 1000),
    (250, "cm", "m", 2.5),
    (12, "in", "ft", 1),
    (1, "mi", "km", 1.609344),
    (1000, "mm", "m", 1),
    (1, "kg", "lb", 2.2046226),
    (16, "oz", "lb", 1),
    (500, "g", "kg", 0.5),
    (100, "C", "F", 212),
    (0, "C", "K", 273.15),
    (32, "F", "C", 0),
    (300, "K", "C", 26.85),
    (5, "m", "m", 5),
])
def test_conversions(convert, value, src, dst, expected):
    assert convert(value, src, dst) == pytest.approx(expected, rel=1e-4, abs=1e-9)


@pytest.mark.parametrize("src, dst", [
    ("kg", "m"),        # different kinds
    ("C", "kg"),        # different kinds
    ("parsec", "m"),    # unknown unit
    ("m", "furlong"),   # unknown unit
])
def test_invalid_conversions_raise_value_error(convert, src, dst):
    with pytest.raises(ValueError):
        convert(1, src, dst)
