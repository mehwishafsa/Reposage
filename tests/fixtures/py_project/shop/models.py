"""Data models."""


class Order:
    """An order placed by a customer."""

    def __init__(self, items):
        self.items = items

    def total(self):
        """Sum of all item prices."""
        return sum(self.price_of(i) for i in self.items)

    def price_of(self, item):
        return item["price"]

    @staticmethod
    def empty():
        return Order([])
