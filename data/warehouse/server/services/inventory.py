from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models import OrderStatus
from storage import Store


MAX_UNITS_PER_ITEM = 20


def validate_available_quantity(store: Store, warehouse_id: str, product_id: str, quantity: int) -> None:
    stock = store.inventory[(warehouse_id, product_id)]
    if quantity > MAX_UNITS_PER_ITEM:
        raise ValueError("ITEM_LIMIT_EXCEEDED")
    if quantity > stock["available"]:
        raise ValueError("INSUFFICIENT_STOCK")


def reserve_order(store: Store, order: dict, hold_minutes: int) -> dict:
    if order["status"] != OrderStatus.DRAFT:
        raise ValueError("ORDER_NOT_EDITABLE")
    for item in order["items"]:
        validate_available_quantity(store, order["warehouseId"], item["productId"], item["quantity"])
    for item in order["items"]:
        stock = store.inventory[(order["warehouseId"], item["productId"])]
        stock["available"] -= item["quantity"]
        stock["reserved"] += item["quantity"]
    reservation_id = store.next_id("reservation", "RES")
    reservation = {
        "reservationId": reservation_id,
        "orderId": order["orderId"],
        "status": "ACTIVE",
        "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=hold_minutes)).isoformat(),
        "items": [
            {
                "orderItemId": item["orderItemId"],
                "productId": item["productId"],
                "quantity": item["quantity"],
            }
            for item in order["items"]
        ],
    }
    store.reservations[reservation_id] = reservation
    order["reservationId"] = reservation_id
    order["status"] = OrderStatus.RESERVED
    return reservation


def consume_reservation(store: Store, order: dict) -> None:
    reservation = store.reservations[order["reservationId"]]
    expires_at = datetime.fromisoformat(reservation["expiresAt"])
    if expires_at <= datetime.now(timezone.utc):
        reservation["status"] = "EXPIRED"
        raise ValueError("RESERVATION_EXPIRED")
    for item in order["items"]:
        stock = store.inventory[(order["warehouseId"], item["productId"])]
        stock["reserved"] -= item["quantity"]
    reservation["status"] = "CONSUMED"


def restore_returned_stock(store: Store, warehouse_id: str, product_id: str, quantity: int) -> None:
    store.inventory[(warehouse_id, product_id)]["available"] += quantity
