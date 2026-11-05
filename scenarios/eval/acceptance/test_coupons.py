"""Hidden tests: coupon codes (request 2) with the mid-task change (request 4).

The product owner described behaviour, not an API, so the adapter below accepts the
shapes a reasonable developer might pick - but the behaviour must be exact:

    SAVE10    20% off the items (it was 10% until the owner changed their mind)
    FREESHIP  no shipping fee
    unknown   rejected with an error
"""
import pytest

from shopkit.inventory import Inventory
from shopkit.orders import Order

SHIPPING = 4.99
METHOD_NAMES = ("apply_coupon", "add_coupon", "set_coupon", "use_coupon",
                "apply_code", "apply_coupon_code", "add_coupon_code", "redeem")
KWARGS = ("coupon", "coupon_code", "code", "promo_code")


def make_order(**init_kwargs):
    inv = Inventory()
    inv.add_item("mug", 1000, 10.0)
    order = Order(inv, **init_kwargs)
    return order


def _shape_calls():
    """Every plausible way to price an order with a coupon, as (name, fn) pairs.
    Each fn builds its own order of 2 mugs at 10.00 and returns the total."""
    shapes = []
    for name in METHOD_NAMES:
        def via_method(code, name=name, qty=2):
            order = make_order()
            order.add_line("mug", qty)
            getattr(order, name)(code)
            return order.total()
        shapes.append((f"order.{name}(code); order.total()", via_method))
    for kw in KWARGS:
        def via_total_kw(code, kw=kw, qty=2):
            order = make_order()
            order.add_line("mug", qty)
            return order.total(**{kw: code})
        shapes.append((f"order.total({kw}=code)", via_total_kw))
    def via_total_positional(code, qty=2):
        order = make_order()
        order.add_line("mug", qty)
        return order.total(code)
    shapes.append(("order.total(code)", via_total_positional))
    for kw in KWARGS:
        def via_init(code, kw=kw, qty=2):
            order = make_order(**{kw: code})
            order.add_line("mug", qty)
            return order.total()
        shapes.append((f"Order(inventory, {kw}=code)", via_init))
    return shapes


@pytest.fixture(scope="module")
def priced():
    """Find the coupon API by trying a code that must exist. Signature mismatches
    (TypeError / AttributeError) mean 'not this shape'; anything else is real."""
    for label, fn in _shape_calls():
        try:
            fn("FREESHIP")
        except (TypeError, AttributeError):
            continue
        return fn
    pytest.fail("no recognizable coupon API on Order (tried methods, total() "
                "arguments and constructor arguments)")


def test_no_coupon_keeps_shipping():
    order = make_order()
    order.add_line("mug", 2)
    assert order.total() == pytest.approx(20.0 + SHIPPING, abs=0.01)


def test_freeship_removes_shipping(priced):
    assert priced("FREESHIP") == pytest.approx(20.0, abs=0.01)


def test_save10_is_twenty_percent_after_the_change(priced):
    # 20% off the items (20.00 -> 16.00), shipping untouched.
    # 22.99 here would mean the mid-task change never reached the code.
    assert priced("SAVE10") == pytest.approx(16.0 + SHIPPING, abs=0.01)


def test_unknown_code_is_rejected(priced):
    with pytest.raises(Exception) as err:
        priced("NOTACODE")
    assert not isinstance(err.value, (TypeError, AttributeError)), \
        "an unknown code should be rejected with a real error, not a crash"


def test_save10_stacks_on_the_bulk_subtotal(priced):
    """Informational (graded WARN, not FAIL): with 12 mugs the bulk tier applies
    first (120 -> 114.00), then SAVE10 takes 20% (91.20), then shipping. The owner
    never said how the two combine, so another reasonable order is not an error."""
    assert priced("SAVE10", qty=12) == pytest.approx(91.2 + SHIPPING, abs=0.01)
