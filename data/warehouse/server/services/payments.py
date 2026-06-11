from __future__ import annotations

from datetime import datetime, timezone

from models import OrderStatus, PaymentRequest
from services.inventory import consume_reservation
from storage import Store


def pay_order(store: Store, order: dict, payload: PaymentRequest, valid_token: str) -> dict:
    existing_id = store.payment_keys.get(payload.idempotencyKey)
    if existing_id:
        existing = store.payments[existing_id]
        if existing["orderId"] != order["orderId"]:
            raise ValueError("IDEMPOTENCY_KEY_CONFLICT")
        return existing
    if order["status"] != OrderStatus.PRICED:
        raise ValueError("ORDER_NOT_READY_FOR_PAYMENT")
    if payload.paymentToken != valid_token:
        raise ValueError("PAYMENT_TOKEN_INVALID")
    if round(payload.amount, 2) != round(order["pricing"]["total"], 2):
        raise ValueError("PAYMENT_AMOUNT_MISMATCH")
    consume_reservation(store, order)
    payment_id = store.next_id("payment", "PAY")
    payment = {
        "paymentId": payment_id,
        "orderId": order["orderId"],
        "amount": round(payload.amount, 2),
        "currency": "RUB",
        "status": "CAPTURED",
        "idempotencyKey": payload.idempotencyKey,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
    }
    store.payments[payment_id] = payment
    store.payment_keys[payload.idempotencyKey] = payment_id
    order["paymentId"] = payment_id
    order["status"] = OrderStatus.PAID
    return payment
