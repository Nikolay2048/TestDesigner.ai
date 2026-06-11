from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


WAREHOUSES = [
    {"warehouseId": "WH-MSK-01", "name": "Москва Север", "regionCode": "RU-MOW"},
    {"warehouseId": "WH-SPB-01", "name": "Санкт-Петербург Центр", "regionCode": "RU-SPE"},
]

PRODUCTS = [
    {"productId": "PRD-1001", "sku": "OFFICE-CHAIR", "name": "Офисное кресло", "unitPrice": 7900.0},
    {"productId": "PRD-1002", "sku": "LED-LAMP", "name": "Настольная LED-лампа", "unitPrice": 1850.0},
    {"productId": "PRD-1003", "sku": "USB-HUB", "name": "USB-C хаб", "unitPrice": 3200.0},
    {"productId": "PRD-1004", "sku": "PAPER-A4", "name": "Бумага A4, коробка", "unitPrice": 2400.0},
    {"productId": "PRD-1005", "sku": "WHITEBOARD", "name": "Маркерная доска", "unitPrice": 5600.0},
]

CUSTOMERS = [
    {
        "customerId": "CUST-REGULAR",
        "name": "ООО Альфа Офис",
        "loyaltyNumber": None,
        "loyaltyLevel": "NONE",
    },
    {
        "customerId": "CUST-LOYAL",
        "name": "АО Северный Контур",
        "loyaltyNumber": "LOY-000777",
        "loyaltyLevel": "GOLD",
    },
]

INITIAL_INVENTORY = {
    ("WH-MSK-01", "PRD-1001"): 8,
    ("WH-MSK-01", "PRD-1002"): 30,
    ("WH-MSK-01", "PRD-1003"): 15,
    ("WH-MSK-01", "PRD-1004"): 20,
    ("WH-MSK-01", "PRD-1005"): 5,
    ("WH-SPB-01", "PRD-1001"): 4,
    ("WH-SPB-01", "PRD-1002"): 12,
    ("WH-SPB-01", "PRD-1003"): 7,
    ("WH-SPB-01", "PRD-1004"): 10,
    ("WH-SPB-01", "PRD-1005"): 3,
}


class Store:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.warehouses = deepcopy(WAREHOUSES)
        self.products = deepcopy(PRODUCTS)
        self.customers = deepcopy(CUSTOMERS)
        self.inventory = {
            key: {"available": quantity, "reserved": 0}
            for key, quantity in INITIAL_INVENTORY.items()
        }
        self.orders: dict[str, dict[str, Any]] = {}
        self.reservations: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, dict[str, Any]] = {}
        self.shipments: dict[str, dict[str, Any]] = {}
        self.deliveries: dict[str, dict[str, Any]] = {}
        self.returns: dict[str, dict[str, Any]] = {}
        self.payment_keys: dict[str, str] = {}
        self.counters = {
            "order": 0,
            "item": 0,
            "reservation": 0,
            "payment": 0,
            "shipment": 0,
            "delivery": 0,
            "return": 0,
        }
        self.lastResetAt = datetime.now(timezone.utc).isoformat()

    def next_id(self, kind: str, prefix: str) -> str:
        self.counters[kind] += 1
        return f"{prefix}-{self.counters[kind]:04d}"


store = Store()
