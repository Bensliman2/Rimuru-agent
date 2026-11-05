"""Hidden tests: the new 50+ tier (request 1) and the boundary bug (request 3).

The agent never sees these. Both requests edit the same function in the same file at
the same time as the coupon work - passing all of them together is the lost-update
check.
"""
import pytest

from shopkit.pricing import bulk_price


# --- request 1: "50 or more of one item should get 15% off" ---------------------
def test_fifty_or_more_gets_fifteen_percent():
    assert bulk_price(50, 10.0) == pytest.approx(425.0, abs=0.01)
    assert bulk_price(100, 2.0) == pytest.approx(170.0, abs=0.01)


def test_forty_nine_still_gets_ten_percent():
    assert bulk_price(49, 10.0) == pytest.approx(441.0, abs=0.01)


# --- request 3: "looks wrong when someone orders exactly 10" ---------------------
def test_exactly_ten_gets_five_percent():
    assert bulk_price(10, 10.0) == pytest.approx(95.0, abs=0.01)


def test_exactly_twenty_five_gets_ten_percent():
    assert bulk_price(25, 10.0) == pytest.approx(225.0, abs=0.01)


# --- existing behaviour that must not move ---------------------------------------
def test_below_ten_has_no_discount():
    assert bulk_price(9, 10.0) == pytest.approx(90.0, abs=0.01)
