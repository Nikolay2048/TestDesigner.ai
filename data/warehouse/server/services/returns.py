from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models import OrderStatus, ReturnStatus
from services.inventory import restore_returned_stock
from storage import Store


RETURN_PERIOD_DAYS = 30


def create_return(store: Store, order: dict, items: list[dict], reason: str) -> dict:
    if order["status"] not in {OrderStatus.DELIVERED, OrderStatus.PARTIALLY_RETURNED}:
        raise ValueError("ORDER_NOT_DELIVERED")
    delivered_at = datetime.fromisoformat(order["deliveredAt"])
    if datetime.now(timezone.utc) > delivered_at + timedelta(days=RETURN_PERIOD_DAYS):
        raise ValueError("RETURN_PERIOD_EXPIRED")
    requested = []
    refund_total = 0.0
    for requested_item in items:
        order_item = next(
            (item for item in order["items"] if item["orderItemId"] == requested_item["orderItemId"]),
            None,
        )
        if not order_item:
            raise KeyError("ORDER_ITEM_NOT_FOUND")
        pending_quantity = sum(
            returned_item["quantity"]
            for existing in store.returns.values()
            if existing["orderId"] == order["orderId"] and existing["status"] == ReturnStatus.REQUESTED
            for returned_item in existing["items"]
            if returned_item["orderItemId"] == order_item["orderItemId"]
        )
        remaining = order_item["quantity"] - order_item["returnedQuantity"] - pending_quantity
        if requested_item["quantity"] > remaining:
            raise ValueError("RETURN_QUANTITY_EXCEEDED")
        line_refund = round(order_item["unitPrice"] * requested_item["quantity"], 2)
        if order["pricing"]["subtotal"]:
            discount_share = order["pricing"]["discount"] / order["pricing"]["subtotal"]
            line_refund = round(line_refund * (1 - discount_share), 2)
        requested.append(
            {
                "orderItemId": order_item["orderItemId"],
                "productId": order_item["productId"],
                "quantity": requested_item["quantity"],
                "refundAmount": line_refund,
            }
        )
        refund_total += line_refund
    return_id = store.next_id("return", "RET")
    result = {
        "returnId": return_id,
        "orderId": order["orderId"],
        "warehouseId": order["warehouseId"],
        "status": ReturnStatus.REQUESTED,
        "reason": reason,
        "items": requested,
        "refundAmount": round(refund_total, 2),
        "currency": "RUB",
        "receivedAt": None,
        "refundId": None,
    }
    store.returns[return_id] = result
    return result


def require_return(store: Store, return_id: str) -> dict:
    result = store.returns.get(return_id)
    if not result:
        raise KeyError("RETURN_NOT_FOUND")
    return result


def receive_return(store: Store, result: dict, warehouse_id: str, received_at: datetime) -> dict:
    if result["status"] != ReturnStatus.REQUESTED:
        raise ValueError("RETURN_NOT_REQUESTED")
    if result["warehouseId"] != warehouse_id:
        raise ValueError("RETURN_WAREHOUSE_MISMATCH")
    now = datetime.now(timezone.utc)
    if received_at.tzinfo is None or received_at > now + timedelta(minutes=5):
        raise ValueError("RETURN_RECEIVED_TIME_INVALID")
    order = store.orders[result["orderId"]]
    for returned_item in result["items"]:
        order_item = next(item for item in order["items"] if item["orderItemId"] == returned_item["orderItemId"])
        order_item["returnedQuantity"] += returned_item["quantity"]
        restore_returned_stock(store, warehouse_id, returned_item["productId"], returned_item["quantity"])
    result["receivedAt"] = received_at.isoformat()
    result["status"] = ReturnStatus.RECEIVED
    order["status"] = OrderStatus.PARTIALLY_RETURNED
    return result


def refund_return(store: Store, result: dict, payment_token: str, valid_token: str) -> dict:
    if result["status"] == ReturnStatus.REFUNDED:
        return result
    if result["status"] != ReturnStatus.RECEIVED:
        raise ValueError("RETURN_NOT_RECEIVED")
    if payment_token != valid_token:
        raise ValueError("PAYMENT_TOKEN_INVALID")
    result["refundId"] = f"RFD-{result['returnId'].split('-')[-1]}"
    result["status"] = ReturnStatus.REFUNDED
    result["refundedAt"] = datetime.now(timezone.utc).isoformat()
    return result
