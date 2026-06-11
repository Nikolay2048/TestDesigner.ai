from __future__ import annotations

from datetime import date, timedelta

from models import CreateOrderRequest, DeliveryType, OrderStatus, ReplaceItemsRequest
from services.catalog import get_customer, get_product, get_warehouse
from storage import Store


DELIVERY_FEES = {DeliveryType.STANDARD: 500.0, DeliveryType.PRIORITY: 1200.0}
MAX_DELIVERY_HORIZON_DAYS = 14


def require_order(store: Store, order_id: str) -> dict:
    order = store.orders.get(order_id)
    if not order:
        raise KeyError("ORDER_NOT_FOUND")
    return order


def _validate_delivery_date(requested: date) -> None:
    today = date.today()
    if requested < today:
        raise ValueError("DELIVERY_DATE_IN_PAST")
    if requested > today + timedelta(days=MAX_DELIVERY_HORIZON_DAYS):
        raise ValueError("DELIVERY_DATE_TOO_FAR")


def _build_items(store: Store, items: list, warehouse_id: str) -> list[dict]:
    if len({item.productId for item in items}) != len(items):
        raise ValueError("DUPLICATE_PRODUCT")
    result = []
    for item in items:
        product = get_product(store, item.productId)
        if not product:
            raise KeyError("PRODUCT_NOT_FOUND")
        if (warehouse_id, item.productId) not in store.inventory:
            raise ValueError("PRODUCT_NOT_STOCKED")
        result.append(
            {
                "orderItemId": store.next_id("item", "ITM"),
                "productId": item.productId,
                "productName": product["name"],
                "quantity": item.quantity,
                "unitPrice": product["unitPrice"],
                "lineTotal": round(product["unitPrice"] * item.quantity, 2),
                "returnedQuantity": 0,
            }
        )
    return result


def create_order(store: Store, payload: CreateOrderRequest) -> dict:
    customer = get_customer(store, payload.customerId)
    if not customer:
        raise KeyError("CUSTOMER_NOT_FOUND")
    if not get_warehouse(store, payload.warehouseId):
        raise KeyError("WAREHOUSE_NOT_FOUND")
    _validate_delivery_date(payload.requestedDeliveryDate)
    if payload.deliveryType == DeliveryType.PRIORITY and customer["loyaltyLevel"] == "NONE":
        raise ValueError("PRIORITY_REQUIRES_LOYALTY")
    order_id = store.next_id("order", "ORD")
    order = {
        "orderId": order_id,
        "customerId": payload.customerId,
        "warehouseId": payload.warehouseId,
        "deliveryType": payload.deliveryType,
        "requestedDeliveryDate": payload.requestedDeliveryDate.isoformat(),
        "status": OrderStatus.DRAFT,
        "items": _build_items(store, payload.items, payload.warehouseId),
        "customerTerms": None,
        "pricing": None,
        "reservationId": None,
        "paymentId": None,
        "shipmentId": None,
    }
    store.orders[order_id] = order
    return order


def replace_items(store: Store, order: dict, payload: ReplaceItemsRequest) -> dict:
    if order["status"] != OrderStatus.DRAFT:
        raise ValueError("ORDER_NOT_EDITABLE")
    order["items"] = _build_items(store, payload.items, order["warehouseId"])
    order["pricing"] = None
    return order


def apply_customer_terms(store: Store, order: dict, loyalty_number: str) -> dict:
    if order["status"] != OrderStatus.DRAFT:
        raise ValueError("ORDER_NOT_EDITABLE")
    customer = get_customer(store, order["customerId"])
    if customer["loyaltyNumber"] != loyalty_number:
        raise ValueError("LOYALTY_NUMBER_MISMATCH")
    order["customerTerms"] = {
        "loyaltyLevel": customer["loyaltyLevel"],
        "discountRate": 0.10 if customer["loyaltyLevel"] == "GOLD" else 0.0,
    }
    return order


def calculate_total(order: dict) -> dict:
    subtotal = round(sum(item["lineTotal"] for item in order["items"]), 2)
    discount_rate = (order["customerTerms"] or {}).get("discountRate", 0.0)
    discount = round(subtotal * discount_rate, 2)
    delivery_fee = DELIVERY_FEES[DeliveryType(order["deliveryType"])]
    total = round(subtotal - discount + delivery_fee, 2)
    return {
        "subtotal": subtotal,
        "discount": discount,
        "deliveryFee": delivery_fee,
        "total": total,
        "currency": "RUB",
    }


def price_order(order: dict) -> dict:
    if order["status"] != OrderStatus.RESERVED:
        raise ValueError("ORDER_NOT_RESERVED")
    order["pricing"] = calculate_total(order)
    order["status"] = OrderStatus.PRICED
    return order
