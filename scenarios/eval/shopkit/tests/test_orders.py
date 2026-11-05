import pytest

from shopkit.inventory import Inventory
from shopkit.orders import SHIPPING_FEE, Order


def stocked():
    inv = Inventory()
    inv.add_item("mug", 100, 10.0)
    inv.add_item("tee", 100, 20.0)
    return inv


def test_total_includes_shipping():
    order = Order(stocked())
    order.add_line("mug", 2)
    assert order.subtotal() == 20.0
    assert order.total() == round(20.0 + SHIPPING_FEE, 2)


def test_lines_are_priced_separately():
    order = Order(stocked())
    order.add_line("mug", 3)
    order.add_line("tee", 1)
    assert order.subtotal() == 50.0


def test_cannot_order_more_than_stock():
    order = Order(stocked())
    with pytest.raises(ValueError):
        order.add_line("mug", 101)
