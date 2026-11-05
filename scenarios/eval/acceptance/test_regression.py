"""Hidden regression tests: behaviour that existed before and must survive.

Duplicates of the repo's own tests on purpose - the agent can edit those, so they
cannot be trusted to guard against regressions on their own.
"""
import pytest

from shopkit.inventory import Inventory
from shopkit.orders import Order
from shopkit.pricing import apply_discount


def test_apply_discount_unchanged():
    assert apply_discount(100, 0.1) == pytest.approx(90.0, abs=0.01)
    with pytest.raises(ValueError):
        apply_discount(100, 1.5)


def test_inventory_basics_unchanged():
    inv = Inventory()
    inv.add_item("mug", 4, 8.5)
    inv.add_item("tee", 2, 15.0)
    inv.remove_item("mug", 3)
    assert inv.quantity("mug") == 1
    assert inv.total_value() == pytest.approx(38.5, abs=0.01)
    assert inv.low_stock(threshold=5) == ["mug", "tee"]
    with pytest.raises(ValueError):
        inv.add_item("mug", 0, 8.5)


def test_order_total_is_subtotal_plus_shipping():
    inv = Inventory()
    inv.add_item("mug", 100, 10.0)
    inv.add_item("tee", 100, 20.0)
    order = Order(inv)
    order.add_line("mug", 3)
    order.add_line("tee", 1)
    assert order.subtotal() == pytest.approx(50.0, abs=0.01)
    assert order.total() == pytest.approx(54.99, abs=0.01)


def test_order_uses_bulk_pricing():
    # 12, not 10: a regression test must pass on the original code, and the
    # original code mis-prices exactly 10 (that is request 3's bug, graded apart).
    inv = Inventory()
    inv.add_item("mug", 100, 10.0)
    order = Order(inv)
    order.add_line("mug", 12)
    assert order.subtotal() == pytest.approx(114.0, abs=0.01)


def test_order_checks_stock():
    inv = Inventory()
    inv.add_item("mug", 5, 10.0)
    with pytest.raises(ValueError):
        Order(inv).add_line("mug", 6)
