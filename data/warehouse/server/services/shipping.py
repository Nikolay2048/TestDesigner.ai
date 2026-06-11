from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models import OrderStatus, ShipmentStatus
from storage import Store


def validate_window(window: dict) -> None:
    starts = window["startsAt"]
    ends = window["endsAt"]
    now = datetime.now(timezone.utc)
    if starts.tzinfo is None or ends.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    if starts <= now:
        raise ValueError("DELIVERY_WINDOW_IN_PAST")
    duration = ends - starts
    if duration < timedelta(hours=1) or duration > timedelta(hours=8):
        raise ValueError("DELIVERY_WINDOW_DURATION_INVALID")
    if starts > now + timedelta(days=14):
        raise ValueError("DELIVERY_WINDOW_TOO_FAR")


def create_shipment(store: Store, order: dict, delivery: dict) -> dict:
    if order["status"] != OrderStatus.PAID:
        raise ValueError("ORDER_NOT_PAID")
    validate_window(delivery["window"])
    shipment_id = store.next_id("shipment", "SHP")
    shipment = {
        "shipmentId": shipment_id,
        "orderId": order["orderId"],
        "warehouseId": order["warehouseId"],
        "deliveryType": order["deliveryType"],
        "status": ShipmentStatus.PREPARED,
        "address": delivery["address"],
        "window": delivery["window"],
        "courierId": None,
        "deliveryId": None,
    }
    store.shipments[shipment_id] = shipment
    order["shipmentId"] = shipment_id
    order["status"] = OrderStatus.READY_TO_SHIP
    return shipment


def require_shipment(store: Store, shipment_id: str) -> dict:
    shipment = store.shipments.get(shipment_id)
    if not shipment:
        raise KeyError("SHIPMENT_NOT_FOUND")
    return shipment


def update_delivery(shipment: dict, delivery: dict) -> dict:
    if shipment["status"] != ShipmentStatus.PREPARED:
        raise ValueError("SHIPMENT_CANNOT_BE_CHANGED")
    validate_window(delivery["window"])
    shipment["address"] = delivery["address"]
    shipment["window"] = delivery["window"]
    return shipment


def handoff(store: Store, shipment: dict, courier_id: str) -> dict:
    if shipment["status"] != ShipmentStatus.PREPARED:
        raise ValueError("SHIPMENT_NOT_PREPARED")
    shipment["courierId"] = courier_id
    shipment["status"] = ShipmentStatus.HANDED_TO_COURIER
    return shipment


def start_delivery(store: Store, shipment: dict, courier_id: str, started_at: datetime) -> dict:
    if shipment["status"] != ShipmentStatus.HANDED_TO_COURIER:
        raise ValueError("SHIPMENT_NOT_HANDED_OFF")
    if shipment["courierId"] != courier_id:
        raise ValueError("COURIER_MISMATCH")
    now = datetime.now(timezone.utc)
    if started_at.tzinfo is None or started_at > now + timedelta(minutes=5):
        raise ValueError("START_TIME_INVALID")
    delivery_id = store.next_id("delivery", "DLV")
    delivery = {
        "deliveryId": delivery_id,
        "shipmentId": shipment["shipmentId"],
        "orderId": shipment["orderId"],
        "courierId": courier_id,
        "status": "IN_TRANSIT",
        "startedAt": started_at.isoformat(),
        "deliveredAt": None,
    }
    store.deliveries[delivery_id] = delivery
    shipment["deliveryId"] = delivery_id
    shipment["status"] = ShipmentStatus.IN_TRANSIT
    return delivery


def complete_delivery(
    store: Store,
    delivery: dict,
    courier_id: str,
    recipient_code: str,
    delivered_at: datetime,
) -> dict:
    if delivery["status"] != "IN_TRANSIT":
        raise ValueError("DELIVERY_NOT_IN_TRANSIT")
    if delivery["courierId"] != courier_id:
        raise ValueError("COURIER_MISMATCH")
    if recipient_code != "654321":
        raise ValueError("RECIPIENT_CODE_INVALID")
    started_at = datetime.fromisoformat(delivery["startedAt"])
    now = datetime.now(timezone.utc)
    if delivered_at.tzinfo is None or delivered_at < started_at or delivered_at > now + timedelta(minutes=5):
        raise ValueError("DELIVERY_TIME_INVALID")
    delivery["status"] = "DELIVERED"
    delivery["deliveredAt"] = delivered_at.isoformat()
    shipment = store.shipments[delivery["shipmentId"]]
    shipment["status"] = ShipmentStatus.DELIVERED
    order = store.orders[delivery["orderId"]]
    order["status"] = OrderStatus.DELIVERED
    order["deliveredAt"] = delivered_at.isoformat()
    return {"delivery": delivery, "shipment": shipment, "order": order}
