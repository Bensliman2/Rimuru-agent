import pytest

from shopkit.inventory import Inventory


def test_add_and_value():
    inv = Inventory()
    inv.add_item("mug", 4, 8.5)
    inv.add_item("tee", 2, 15.0)
    assert inv.quantity("mug") == 4
    assert inv.total_value() == 64.0


def test_remove_item():
    inv = Inventory()
    inv.add_item("mug", 4, 8.5)
    inv.remove_item("mug", 3)
    assert inv.quantity("mug") == 1


def test_low_stock():
    inv = Inventory()
    inv.add_item("mug", 2, 8.5)
    inv.add_item("tee", 20, 15.0)
    assert inv.low_stock(threshold=5) == ["mug"]


def test_rejects_bad_input():
    inv = Inventory()
    with pytest.raises(ValueError):
        inv.add_item("mug", 0, 8.5)
    with pytest.raises(ValueError):
        inv.add_item("mug", 1, -1)
