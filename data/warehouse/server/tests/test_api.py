from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


def future_window(days: int = 2) -> dict:
    starts = datetime.now(timezone.utc) + timedelta(days=days)
    return {
        "startsAt": starts.isoformat(),
        "endsAt": (starts + timedelta(hours=3)).isoformat(),
    }


def address(building: str = "10") -> dict:
    return {
        "countryCode": "RU",
        "regionCode": "RU-MOW",
        "city": "Москва",
        "street": "Ленинградский проспект",
        "building": building,
        "postalCode": "125040",
    }


def create_prepared_order(*, loyal: bool = False) -> dict:
    customer = "CUST-LOYAL" if loyal else "CUST-REGULAR"
    delivery_type = "PRIORITY" if loyal else "STANDARD"
    create = client.post(
        "/orders",
        json={
            "customerId": customer,
            "warehouseId": "WH-MSK-01",
            "deliveryType": delivery_type,
            "requestedDeliveryDate": (date.today() + timedelta(days=2)).isoformat(),
            "items": [
                {"productId": "PRD-1002", "quantity": 2},
                {"productId": "PRD-1003", "quantity": 1},
            ],
        },
    )
    assert create.status_code == 201, create.text
    order = create.json()
    if loyal:
        terms = client.post(
            f"/orders/{order['orderId']}/customer-terms",
            json={"loyaltyNumber": "LOY-000777"},
        )
        assert terms.status_code == 200, terms.text
    reserve = client.post(f"/orders/{order['orderId']}/reservations", json={"holdMinutes": 30})
    assert reserve.status_code == 201, reserve.text
    quote = client.post(f"/orders/{order['orderId']}/quote")
    assert quote.status_code == 200, quote.text
    priced = quote.json()
    payment_payload = {
        "amount": priced["pricing"]["total"],
        "paymentToken": "tok_warehouse_demo_2026",
        "idempotencyKey": f"idem-{order['orderId']}",
    }
    payment = client.post(f"/orders/{order['orderId']}/payments", json=payment_payload)
    assert payment.status_code == 201, payment.text
    shipment = client.post(
        f"/orders/{order['orderId']}/shipments",
        json={"delivery": {"address": address(), "window": future_window()}},
    )
    assert shipment.status_code == 201, shipment.text
    return {
        "order": client.get(f"/orders/{order['orderId']}").json(),
        "payment": payment.json(),
        "paymentPayload": payment_payload,
        "shipment": shipment.json(),
    }


def deliver(prepared: dict) -> dict:
    shipment_id = prepared["shipment"]["shipmentId"]
    handoff = client.post(f"/shipments/{shipment_id}/handoff", json={"courierId": "COUR-0042"})
    assert handoff.status_code == 200, handoff.text
    started = datetime.now(timezone.utc)
    delivery = client.post(
        f"/shipments/{shipment_id}/deliveries/start",
        json={"courierId": "COUR-0042", "startedAt": started.isoformat()},
    )
    assert delivery.status_code == 201, delivery.text
    completed = client.post(
        f"/deliveries/{delivery.json()['deliveryId']}/complete",
        json={
            "courierId": "COUR-0042",
            "recipientCode": "654321",
            "deliveredAt": (started + timedelta(minutes=1)).isoformat(),
        },
    )
    assert completed.status_code == 200, completed.text
    return completed.json()


def setup_function() -> None:
    client.post("/mock/reset")


def test_reset_restores_state_and_counters() -> None:
    first = create_prepared_order()
    assert first["order"]["orderId"] == "ORD-0001"
    reset = client.post("/mock/reset")
    assert reset.status_code == 200
    second = create_prepared_order()
    assert second["order"]["orderId"] == "ORD-0001"


def test_standard_order_happy_path() -> None:
    result = create_prepared_order()
    assert result["order"]["status"] == "READY_TO_SHIP"
    assert result["shipment"]["status"] == "PREPARED"
    assert result["order"]["pricing"] == {
        "subtotal": 6900.0,
        "discount": 0.0,
        "deliveryFee": 500.0,
        "total": 7400.0,
        "currency": "RUB",
    }


def test_priority_order_applies_discount_and_priority_fee() -> None:
    result = create_prepared_order(loyal=True)
    assert result["order"]["customerTerms"]["loyaltyLevel"] == "GOLD"
    assert result["order"]["pricing"]["discount"] == 690.0
    assert result["order"]["pricing"]["deliveryFee"] == 1200.0
    assert result["order"]["pricing"]["total"] == 7410.0
    assert result["shipment"]["deliveryType"] == "PRIORITY"


def test_prepared_shipment_delivery_details_can_change() -> None:
    result = create_prepared_order()
    shipment_id = result["shipment"]["shipmentId"]
    changed = client.patch(
        f"/shipments/{shipment_id}/delivery",
        json={"address": address("25"), "window": future_window(3)},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["address"]["building"] == "25"


def test_delivery_completion_updates_all_states() -> None:
    completed = deliver(create_prepared_order())
    assert completed["delivery"]["status"] == "DELIVERED"
    assert completed["shipment"]["status"] == "DELIVERED"
    assert completed["order"]["status"] == "DELIVERED"


def test_partial_return_refund_and_stock_recalculation() -> None:
    before = client.get("/warehouses/WH-MSK-01/products").json()["items"]
    before_lamps = next(item for item in before if item["productId"] == "PRD-1002")["available"]
    completed = deliver(create_prepared_order())
    order = completed["order"]
    lamp_item = next(item for item in order["items"] if item["productId"] == "PRD-1002")
    created = client.post(
        f"/orders/{order['orderId']}/returns",
        json={
            "reason": "Одна лампа не подошла по комплектации",
            "items": [{"orderItemId": lamp_item["orderItemId"], "quantity": 1}],
        },
    )
    assert created.status_code == 201, created.text
    result = created.json()
    assert result["refundAmount"] == 1850.0
    received = client.post(
        f"/returns/{result['returnId']}/receive",
        json={"warehouseId": "WH-MSK-01", "receivedAt": datetime.now(timezone.utc).isoformat()},
    )
    assert received.status_code == 200, received.text
    refunded = client.post(
        f"/returns/{result['returnId']}/refund",
        json={"paymentToken": "tok_warehouse_demo_2026"},
    )
    assert refunded.status_code == 200
    assert refunded.json()["status"] == "REFUNDED"
    after = client.get("/warehouses/WH-MSK-01/products").json()["items"]
    after_lamps = next(item for item in after if item["productId"] == "PRD-1002")["available"]
    assert after_lamps == before_lamps - 1


def test_invalid_state_transitions_are_rejected() -> None:
    created = client.post(
        "/orders",
        json={
            "customerId": "CUST-REGULAR",
            "warehouseId": "WH-MSK-01",
            "deliveryType": "STANDARD",
            "requestedDeliveryDate": (date.today() + timedelta(days=1)).isoformat(),
            "items": [{"productId": "PRD-1002", "quantity": 1}],
        },
    ).json()
    payment = client.post(
        f"/orders/{created['orderId']}/payments",
        json={
            "amount": 2350,
            "paymentToken": "tok_warehouse_demo_2026",
            "idempotencyKey": "invalid-state-payment",
        },
    )
    assert payment.status_code == 409
    assert payment.json()["detail"]["code"] == "ORDER_NOT_READY_FOR_PAYMENT"

    prepared = create_prepared_order()
    shipment_id = prepared["shipment"]["shipmentId"]
    not_started = client.post(
        "/deliveries/DLV-9999/complete",
        json={
            "courierId": "COUR-0042",
            "recipientCode": "654321",
            "deliveredAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert not_started.status_code == 404
    client.post(f"/shipments/{shipment_id}/handoff", json={"courierId": "COUR-0042"})
    changed = client.patch(
        f"/shipments/{shipment_id}/delivery",
        json={"address": address("30"), "window": future_window()},
    )
    assert changed.status_code == 409


def test_payment_is_idempotent() -> None:
    prepared = create_prepared_order()
    order_id = prepared["order"]["orderId"]
    repeat = client.post(f"/orders/{order_id}/payments", json=prepared["paymentPayload"])
    assert repeat.status_code == 201
    assert repeat.json()["paymentId"] == prepared["payment"]["paymentId"]
    state = client.get("/mock/state").json()
    assert len(state["payments"]) == 1


def test_pending_returns_cannot_exceed_delivered_quantity() -> None:
    completed = deliver(create_prepared_order())
    order = completed["order"]
    lamp_item = next(item for item in order["items"] if item["productId"] == "PRD-1002")
    first = client.post(
        f"/orders/{order['orderId']}/returns",
        json={
            "reason": "Первая лампа не подошла по комплектации",
            "items": [{"orderItemId": lamp_item["orderItemId"], "quantity": 1}],
        },
    )
    assert first.status_code == 201
    second = client.post(
        f"/orders/{order['orderId']}/returns",
        json={
            "reason": "Попытка повторно вернуть весь исходный объём",
            "items": [{"orderItemId": lamp_item["orderItemId"], "quantity": 2}],
        },
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "RETURN_QUANTITY_EXCEEDED"


def test_openapi_contains_critical_contracts() -> None:
    schema = app.openapi()
    assert schema["paths"]["/orders"]["post"]["operationId"] == "createOrder"
    assert schema["components"]["schemas"]["CreateOrderRequest"]["required"]
    assert schema["components"]["schemas"]["CompleteDeliveryRequest"]["properties"]["recipientCode"]["pattern"] == r"^\d{6}$"
    actual = create_prepared_order()
    order_schema = schema["components"]["schemas"]["CreateOrderRequest"]
    assert set(order_schema["required"]).issubset(
        {"customerId", "warehouseId", "deliveryType", "requestedDeliveryDate", "items"}
    )
    assert actual["shipment"]["shipmentId"].startswith("SHP-")
