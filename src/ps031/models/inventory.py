from dataclasses import dataclass
from decimal import Decimal


@dataclass
class InventoryItem:
    sku_id: str
    name: str
    category: str
    brand: str
    price: Decimal
    stock: int

    def __post_init__(self) -> None:
        if not isinstance(self.price, Decimal):
            self.price = Decimal(str(self.price))
        if self.price < Decimal("0.00"):
            raise ValueError(f"price cannot be negative: {self.price}")
        if self.stock < 0:
            raise ValueError(f"stock cannot be negative: {self.stock}")
