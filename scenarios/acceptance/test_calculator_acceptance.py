"""Hidden acceptance tests for the calculator the agent is asked to build.

The agent never sees this file - it lives outside the workspace its tools are
confined to - so it cannot write code that merely satisfies these assertions.
They check the spec from the request, plus the floor-division requirement that
arrived later as a follow-up: that message passed straight through to the
worker without the Router seeing it, so passing it proves the pass-through path
actually changed what the worker built.
"""
import importlib
import os
import re
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(os.environ.get("RIMURU_WORKSPACE", "data/workspace")).resolve()
SOURCE = WORKSPACE / "calc" / "calculator.py"


@pytest.fixture(scope="module")
def evaluate():
    if not SOURCE.exists():
        elsewhere = sorted(WORKSPACE.rglob("calculator.py"))
        hint = (f" - it wrote {elsewhere[0].relative_to(WORKSPACE)} instead"
                if elsewhere else "")
        pytest.fail(f"the agent did not create {SOURCE}{hint}")
    sys.path.insert(0, str(WORKSPACE))
    try:
        module = importlib.import_module("calc.calculator")
    except ImportError:
        sys.path.insert(0, str(WORKSPACE / "calc"))
        module = importlib.import_module("calculator")
    return module.evaluate


@pytest.mark.parametrize("expr, expected", [
    ("1 + 2", 3),
    ("2 * (3 + 4)", 14),
    ("7 / 2", 3.5),
    ("10 % 4", 2),
    ("-3 + 5", 2),
    ("1.5 * 4", 6),
    ("((2))", 2),
    ("2 + 3 * 4 - 6 / 2", 11),
    ("2 ** 3 ** 2", 512),       # ** is right-associative, as in Python
    ("-2 ** 2", -4),            # ** binds tighter than unary minus, as in Python
    ("  8  -  2  ", 6),
])
def test_arithmetic(evaluate, expr, expected):
    assert evaluate(expr) == pytest.approx(expected)


def test_floor_division_requested_in_the_follow_up(evaluate):
    assert evaluate("7 // 2") == pytest.approx(3)
    assert evaluate("-7 // 2") == pytest.approx(-4)


@pytest.mark.parametrize("expr", ["2 +", "abc", "(1 + 2", "1 + * 2", ""])
def test_malformed_input_raises_value_error(evaluate, expr):
    with pytest.raises(ValueError):
        evaluate(expr)


def test_division_by_zero(evaluate):
    with pytest.raises(ZeroDivisionError):
        evaluate("1 / 0")


def test_input_cannot_run_code(evaluate):
    with pytest.raises(ValueError):
        evaluate("__import__('os').getcwd()")


def test_source_uses_no_eval_or_exec(evaluate):
    source = SOURCE.read_text(encoding="utf-8")
    assert not re.search(r"(?<![\w.])(eval|exec)\s*\(", source)
