"""Orders: lines of items, priced with bulk discounts, plus shipping."""
from .pricing import bulk_price

SHIPPING_FEE = 4.99


class Order:
    def __init__(self, inventory):
        self.inventory = inventory
        self.lines = []  # (sku, qty)

    def add_line(self, sku, qty):
        if qty <= 0:
            raise ValueError("qty must be positive")
        if self.inventory.quantity(sku) < qty:
            raise ValueError(f"not enough {sku} in stock")
        self.lines.append((sku, qty))

    def subtotal(self):
        return round(sum(bulk_price(q, self.inventory.price(s)) for s, q in self.lines), 2)

    def total(self):
        return round(self.subtotal() + SHIPPING_FEE, 2)
