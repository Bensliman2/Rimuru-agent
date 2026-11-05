from shopkit.pricing import apply_discount, bulk_price


def test_apply_discount():
    assert apply_discount(100, 0.1) == 90.0


def test_no_bulk_discount_below_ten():
    assert bulk_price(9, 10.0) == 90.0


def test_bulk_discount_at_ten():
    assert bulk_price(10, 10.0) == 95.0


def test_bulk_discount_at_twenty_five():
    assert bulk_price(25, 10.0) == 225.0
