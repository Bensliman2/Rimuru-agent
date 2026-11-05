"""Stock levels and prices."""


class Inventory:
    def __init__(self):
        self._items = {}  # sku -> {"qty": int, "price": float}

    def add_item(self, sku, qty, price):
        if qty <= 0:
            raise ValueError("qty must be positive")
        if price < 0:
            raise ValueError("price cannot be negative")
        item = self._items.setdefault(sku, {"qty": 0, "price": price})
        item["qty"] += qty
        item["price"] = price

    def remove_item(self, sku, qty):
        if sku not in self._items:
            raise KeyError(sku)
        self._items[sku]["qty"] -= qty

    def quantity(self, sku):
        return self._items.get(sku, {"qty": 0})["qty"]

    def price(self, sku):
        return self._items[sku]["price"]

    def total_value(self):
        return round(sum(i["qty"] * i["price"] for i in self._items.values()), 2)

    def low_stock(self, threshold=5):
        return sorted(sku for sku, i in self._items.items() if i["qty"] < threshold)
