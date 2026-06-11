from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx


REGULAR_CUSTOMER_ID = "CUST-REGULAR"
LOYAL_CUSTOMER_ID = "CUST-LOYAL"
LOYALTY_NUMBER = "LOY-000777"
WAREHOUSE_ID = "WH-MSK-01"
PAYMENT_TOKEN = "tok_warehouse_demo_2026"
COURIER_ID = "COUR-0042"
RECIPIENT_CODE = "654321"


@dataclass
class ScenarioContext:
    customer_id: str
    warehouse_id: str
    order_id: str
    order_item_ids: list[str]
    reservation_id: str
    payment_id: str
    shipment_id: str
    delivery_id: str | None = None
    return_id: str | None = None


class WarehouseApi:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0)

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.client.request(method, path, **kwargs)
        print(f"{method.upper():5} {path} -> {response.status_code}")
        if response.is_error:
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        response.raise_for_status()
        return response.json()

    def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("PATCH", path, **kwargs)


def future_delivery_date(days: int = 2) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def future_window(days: int = 2) -> dict[str, str]:
    starts_at = datetime.now(timezone.utc) + timedelta(days=days)
    return {
        "startsAt": starts_at.isoformat(),
        "endsAt": (starts_at + timedelta(hours=3)).isoformat(),
    }


def delivery_address(building: str = "10") -> dict[str, str]:
    return {
        "countryCode": "RU",
        "regionCode": "RU-MOW",
        "city": "Москва",
        "street": "Ленинградский проспект",
        "building": building,
        "postalCode": "125040",
    }


def reset(api: WarehouseApi) -> None:
    result = api.post("/mock/reset")
    print(f"State reset at {result['resetAt']}\n")


def scenario_1_standard_order(api: WarehouseApi) -> ScenarioContext:
    print("=== Scenario 1: standard order ===")
    warehouses = api.get("/warehouses")
    warehouse = next(
        item for item in warehouses["items"] if item["warehouseId"] == WAREHOUSE_ID
    )

    stock = api.get(
        f"/warehouses/{warehouse['warehouseId']}/products",
        params={"availableOnly": True},
    )
    selected_products = [
        next(item for item in stock["items"] if item["productId"] == "PRD-1002"),
        next(item for item in stock["items"] if item["productId"] == "PRD-1003"),
    ]
    api.get(f"/products/{selected_products[0]['productId']}")

    order = api.post(
        "/orders",
        json={
            "customerId": REGULAR_CUSTOMER_ID,
            "warehouseId": warehouse["warehouseId"],
            "deliveryType": "STANDARD",
            "requestedDeliveryDate": future_delivery_date(),
            "items": [
                {"productId": selected_products[0]["productId"], "quantity": 2},
                {"productId": selected_products[1]["productId"], "quantity": 1},
            ],
        },
    )
    order_id = order["orderId"]

    reservation = api.post(
        f"/orders/{order_id}/reservations",
        json={"holdMinutes": 30},
    )
    priced_order = api.post(f"/orders/{order_id}/quote")
    payment = api.post(
        f"/orders/{order_id}/payments",
        json={
            "amount": priced_order["pricing"]["total"],
            "paymentToken": PAYMENT_TOKEN,
            "idempotencyKey": f"standard-payment-{order_id}",
        },
    )
    shipment = api.post(
        f"/orders/{order_id}/shipments",
        json={
            "delivery": {
                "address": delivery_address(),
                "window": future_window(),
            }
        },
    )
    final_order = api.get(f"/orders/{order_id}")
    assert final_order["status"] == "READY_TO_SHIP"
    assert shipment["status"] == "PREPARED"
    print(
        f"Checkpoint: order={order_id} READY_TO_SHIP, "
        f"shipment={shipment['shipmentId']} PREPARED, "
        f"total={final_order['pricing']['total']} RUB\n"
    )
    return ScenarioContext(
        customer_id=REGULAR_CUSTOMER_ID,
        warehouse_id=warehouse["warehouseId"],
        order_id=order_id,
        order_item_ids=[item["orderItemId"] for item in final_order["items"]],
        reservation_id=reservation["reservationId"],
        payment_id=payment["paymentId"],
        shipment_id=shipment["shipmentId"],
    )


def scenario_2_priority_order(api: WarehouseApi) -> ScenarioContext:
    print("=== Scenario 2: priority loyalty order ===")
    order = api.post(
        "/orders",
        json={
            "customerId": LOYAL_CUSTOMER_ID,
            "warehouseId": WAREHOUSE_ID,
            "deliveryType": "PRIORITY",
            "requestedDeliveryDate": future_delivery_date(),
            "items": [
                {"productId": "PRD-1002", "quantity": 2},
                {"productId": "PRD-1003", "quantity": 1},
            ],
        },
    )
    order_id = order["orderId"]
    api.post(
        f"/orders/{order_id}/customer-terms",
        json={"loyaltyNumber": LOYALTY_NUMBER},
    )
    reservation = api.post(
        f"/orders/{order_id}/reservations",
        json={"holdMinutes": 30},
    )
    priced_order = api.post(f"/orders/{order_id}/quote")
    pricing = priced_order["pricing"]
    payment = api.post(
        f"/orders/{order_id}/payments",
        json={
            "amount": pricing["total"],
            "paymentToken": PAYMENT_TOKEN,
            "idempotencyKey": f"priority-payment-{order_id}",
        },
    )
    shipment = api.post(
        f"/orders/{order_id}/shipments",
        json={
            "delivery": {
                "address": delivery_address("15"),
                "window": future_window(),
            }
        },
    )
    final_order = api.get(f"/orders/{order_id}")
    assert final_order["status"] == "READY_TO_SHIP"
    assert shipment["status"] == "PREPARED"
    assert shipment["deliveryType"] == "PRIORITY"
    print(
        f"Checkpoint: subtotal={pricing['subtotal']}, discount={pricing['discount']}, "
        f"deliveryFee={pricing['deliveryFee']}, total={pricing['total']} RUB\n"
    )
    return ScenarioContext(
        customer_id=LOYAL_CUSTOMER_ID,
        warehouse_id=WAREHOUSE_ID,
        order_id=order_id,
        order_item_ids=[item["orderItemId"] for item in final_order["items"]],
        reservation_id=reservation["reservationId"],
        payment_id=payment["paymentId"],
        shipment_id=shipment["shipmentId"],
    )


def scenario_3_change_delivery(
    api: WarehouseApi,
    context: ScenarioContext,
) -> ScenarioContext:
    print("=== Scenario 3: change prepared shipment delivery ===")
    order = api.get(f"/orders/{context.order_id}")
    shipment = api.get(f"/shipments/{context.shipment_id}")
    assert order["status"] == "READY_TO_SHIP"
    assert shipment["status"] == "PREPARED"

    changed = api.patch(
        f"/shipments/{context.shipment_id}/delivery",
        json={
            "address": delivery_address("25"),
            "window": future_window(days=3),
        },
    )
    assert changed["shipmentId"] == context.shipment_id
    assert changed["status"] == "PREPARED"
    assert changed["address"]["building"] == "25"
    print(
        f"Checkpoint: shipment={context.shipment_id} remains PREPARED, "
        "building changed to 25\n"
    )
    return context


def scenario_4_complete_delivery(
    api: WarehouseApi,
    context: ScenarioContext,
) -> ScenarioContext:
    print("=== Scenario 4: complete delivery ===")
    api.post(
        f"/shipments/{context.shipment_id}/handoff",
        json={"courierId": COURIER_ID},
    )
    started_at = datetime.now(timezone.utc)
    delivery = api.post(
        f"/shipments/{context.shipment_id}/deliveries/start",
        json={
            "courierId": COURIER_ID,
            "startedAt": started_at.isoformat(),
        },
    )
    context.delivery_id = delivery["deliveryId"]

    completed = api.post(
        f"/deliveries/{context.delivery_id}/complete",
        json={
            "courierId": COURIER_ID,
            "recipientCode": RECIPIENT_CODE,
            "deliveredAt": (started_at + timedelta(minutes=1)).isoformat(),
        },
    )
    assert completed["order"]["status"] == "DELIVERED"
    assert completed["shipment"]["status"] == "DELIVERED"
    assert completed["delivery"]["status"] == "DELIVERED"
    print(
        f"Checkpoint: order={context.order_id}, shipment={context.shipment_id}, "
        f"delivery={context.delivery_id} are DELIVERED\n"
    )
    return context


def scenario_5_partial_return(
    api: WarehouseApi,
    context: ScenarioContext,
) -> ScenarioContext:
    print("=== Scenario 5: partial return ===")
    order = api.get(f"/orders/{context.order_id}")
    assert order["status"] == "DELIVERED"
    returned_item = next(
        item for item in order["items"] if item["productId"] == "PRD-1002"
    )

    created_return = api.post(
        f"/orders/{context.order_id}/returns",
        json={
            "reason": "Одна лампа не подошла по комплектации",
            "items": [
                {
                    "orderItemId": returned_item["orderItemId"],
                    "quantity": 1,
                }
            ],
        },
    )
    context.return_id = created_return["returnId"]
    api.post(
        f"/returns/{context.return_id}/receive",
        json={
            "warehouseId": context.warehouse_id,
            "receivedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    refunded = api.post(
        f"/returns/{context.return_id}/refund",
        json={"paymentToken": PAYMENT_TOKEN},
    )
    assert refunded["status"] == "REFUNDED"
    updated_order = api.get(f"/orders/{context.order_id}")
    assert updated_order["status"] == "PARTIALLY_RETURNED"
    print(
        f"Checkpoint: return={context.return_id} REFUNDED, "
        f"refundAmount={refunded['refundAmount']} RUB, "
        f"order={context.order_id} PARTIALLY_RETURNED\n"
    )
    return context


def run_full_chain(api: WarehouseApi) -> ScenarioContext:
    context = scenario_1_standard_order(api)
    scenario_3_change_delivery(api, context)
    scenario_4_complete_delivery(api, context)
    scenario_5_partial_return(api, context)
    return context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run executable happy path examples against the warehouse API."
    )
    parser.add_argument(
        "scenario",
        choices=("standard", "priority", "full", "all"),
        nargs="?",
        default="all",
        help="standard=1, priority=2, full=1->3->4->5, all=full plus independent 2",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
        help="Warehouse API base URL.",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not reset server state before the first independent chain.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api = WarehouseApi(args.base_url)
    try:
        if not args.no_reset:
            reset(api)

        if args.scenario == "standard":
            scenario_1_standard_order(api)
        elif args.scenario == "priority":
            scenario_2_priority_order(api)
        elif args.scenario == "full":
            run_full_chain(api)
        else:
            run_full_chain(api)
            reset(api)
            scenario_2_priority_order(api)
    finally:
        api.close()


if __name__ == "__main__":
    main()
