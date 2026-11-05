"""Pricing rules."""

# (minimum quantity, discount) - checked from the largest tier down.
BULK_TIERS = [(25, 0.10), (10, 0.05)]


def apply_discount(price, pct):
    """Return price reduced by pct (0.1 means 10% off), rounded to cents."""
    if not 0 <= pct <= 1:
        raise ValueError("pct must be between 0 and 1")
    return round(price * (1 - pct), 2)


def bulk_price(qty, unit_price):
    """Total for qty units. 10 or more units get 5% off, 25 or more get 10% off."""
    total = qty * unit_price
    for min_qty, pct in BULK_TIERS:
        if qty > min_qty:
            return apply_discount(total, pct)
    return round(total, 2)
