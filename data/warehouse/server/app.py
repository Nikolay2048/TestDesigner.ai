from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from models import (
    CompleteDeliveryRequest,
    CourierHandoffRequest,
    CreateOrderRequest,
    CreateReturnRequest,
    CreateShipmentRequest,
    CustomerTermsRequest,
    DeliveryDetailsRequest,
    PaymentRequest,
    ReceiveReturnRequest,
    RefundReturnRequest,
    ReplaceItemsRequest,
    ReserveOrderRequest,
    StartDeliveryRequest,
    UpdateDeliveryRequest,
)
from services import catalog, inventory, orders, payments, returns, shipping
from storage import store


PAYMENT_TOKEN = "tok_warehouse_demo_2026"
VALID_COURIER = "COUR-0042"

app = FastAPI(
    title="Warehouse Order and Delivery API",
    version="1.0.0",
    description="Stateful B2B warehouse order, delivery, and return demonstration service.",
)


def error(
    status: int,
    code: str,
    message: str,
    *,
    field: str | None = None,
    hint: str | None = None,
    current_state: str | None = None,
    allowed_states: list[str] | None = None,
) -> HTTPException:
    detail = {"code": code, "message": message}
    if field:
        detail["field"] = field
    if hint:
        detail["hint"] = hint
    if current_state:
        detail["currentState"] = current_state
    if allowed_states:
        detail["allowedStates"] = allowed_states
    return HTTPException(status_code=status, detail=detail)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Any, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    path = ".".join(str(part) for part in first["loc"] if part != "body")
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "REQUEST_VALIDATION_ERROR",
                "message": first["msg"],
                "field": path or None,
            }
        },
    )


def translate(exc: Exception, resource: dict | None = None) -> HTTPException:
    code = str(exc.args[0])
    not_found = {
        "ORDER_NOT_FOUND": "Order was not found.",
        "PRODUCT_NOT_FOUND": "Product was not found.",
        "CUSTOMER_NOT_FOUND": "Customer was not found.",
        "WAREHOUSE_NOT_FOUND": "Warehouse was not found.",
        "SHIPMENT_NOT_FOUND": "Shipment was not found.",
        "RETURN_NOT_FOUND": "Return was not found.",
        "ORDER_ITEM_NOT_FOUND": "Order item was not found.",
    }
    if code in not_found:
        return error(404, code, not_found[code])
    messages = {
        "DUPLICATE_PRODUCT": "Each product may appear only once in an order.",
        "PRODUCT_NOT_STOCKED": "The selected product is not stocked by this warehouse.",
        "DELIVERY_DATE_IN_PAST": "Requested delivery date cannot be in the past.",
        "DELIVERY_DATE_TOO_FAR": "Requested delivery date exceeds the planning horizon.",
        "PRIORITY_REQUIRES_LOYALTY": "Priority delivery is available only to loyalty customers.",
        "ORDER_NOT_EDITABLE": "The order can no longer be edited or reserved.",
        "ITEM_LIMIT_EXCEEDED": "A single order line cannot exceed 20 units.",
        "INSUFFICIENT_STOCK": "Requested quantity exceeds currently available stock.",
        "LOYALTY_NUMBER_MISMATCH": "The loyalty number does not belong to this customer.",
        "ORDER_NOT_RESERVED": "The order must be fully reserved before pricing.",
        "ORDER_NOT_READY_FOR_PAYMENT": "The order must have a current calculated price before payment.",
        "PAYMENT_TOKEN_INVALID": "The payment token is not accepted.",
        "PAYMENT_AMOUNT_MISMATCH": "Payment amount does not match the calculated order total.",
        "IDEMPOTENCY_KEY_CONFLICT": "The idempotency key was already used for another order.",
        "RESERVATION_EXPIRED": "The inventory reservation has expired.",
        "ORDER_NOT_PAID": "A shipment can be created only for a paid order.",
        "DELIVERY_WINDOW_IN_PAST": "Delivery window must begin in the future.",
        "DELIVERY_WINDOW_DURATION_INVALID": "Delivery window duration must be between one and eight hours.",
        "DELIVERY_WINDOW_TOO_FAR": "Delivery window exceeds the supported planning horizon.",
        "TIMEZONE_REQUIRED": "Delivery timestamps must include a timezone.",
        "SHIPMENT_CANNOT_BE_CHANGED": "Delivery details cannot be changed after courier handoff.",
        "SHIPMENT_NOT_PREPARED": "Only a prepared shipment can be handed to a courier.",
        "SHIPMENT_NOT_HANDED_OFF": "Delivery can start only after courier handoff.",
        "COURIER_MISMATCH": "Courier identifier does not match the assigned courier.",
        "START_TIME_INVALID": "Delivery start time is invalid.",
        "DELIVERY_NOT_IN_TRANSIT": "Only an in-transit delivery can be completed.",
        "RECIPIENT_CODE_INVALID": "Recipient confirmation code is invalid.",
        "DELIVERY_TIME_INVALID": "Actual delivery time is inconsistent with the delivery start.",
        "ORDER_NOT_DELIVERED": "Returns are accepted only for delivered orders.",
        "RETURN_PERIOD_EXPIRED": "The return period has expired.",
        "RETURN_QUANTITY_EXCEEDED": "Returned quantity exceeds the remaining delivered quantity.",
        "RETURN_NOT_REQUESTED": "Only a requested return can be received.",
        "RETURN_WAREHOUSE_MISMATCH": "Return must be received at the warehouse that fulfilled the order.",
        "RETURN_RECEIVED_TIME_INVALID": "Return receipt time is invalid.",
        "RETURN_NOT_RECEIVED": "Refund is available only after the goods are received.",
    }
    current = str(resource.get("status")) if resource else None
    return error(
        409
        if any(marker in code for marker in ("NOT_", "MISMATCH", "EXPIRED", "CANNOT", "EXCEEDED", "CONFLICT"))
        else 422,
        code,
        messages.get(code, "The request violates a domain constraint."),
        current_state=current,
    )


@app.get("/warehouses", operation_id="listWarehouses", summary="List fulfillment warehouses")
def list_warehouses() -> dict:
    return {"items": catalog.list_warehouses(store)}


@app.get("/warehouses/{warehouse_id}/products", operation_id="listWarehouseProducts", summary="List products and stock")
def list_products(warehouse_id: str, availableOnly: bool = Query(default=True)) -> dict:
    if not catalog.get_warehouse(store, warehouse_id):
        raise error(404, "WAREHOUSE_NOT_FOUND", "Warehouse was not found.")
    items = catalog.available_products(store, warehouse_id)
    if availableOnly:
        items = [item for item in items if item["available"] > 0]
    return {"items": items}


@app.get("/products/{product_id}", operation_id="getProduct", summary="Get product card")
def get_product(product_id: str) -> dict:
    product = catalog.get_product(store, product_id)
    if not product:
        raise error(404, "PRODUCT_NOT_FOUND", "Product was not found.")
    return product


@app.post("/orders", status_code=201, operation_id="createOrder", summary="Create an editable order")
def create_order(payload: CreateOrderRequest) -> dict:
    try:
        return orders.create_order(store, payload)
    except (KeyError, ValueError) as exc:
        raise translate(exc) from exc


@app.get("/orders/{order_id}", operation_id="getOrder", summary="Get order with items and calculated amounts")
def get_order(order_id: str) -> dict:
    try:
        return orders.require_order(store, order_id)
    except KeyError as exc:
        raise translate(exc) from exc


@app.put("/orders/{order_id}/items", operation_id="replaceOrderItems", summary="Replace items of an editable order")
def replace_order_items(order_id: str, payload: ReplaceItemsRequest) -> dict:
    order = get_order(order_id)
    try:
        return orders.replace_items(store, order, payload)
    except (KeyError, ValueError) as exc:
        raise translate(exc, order) from exc


@app.post("/orders/{order_id}/customer-terms", operation_id="applyCustomerTerms", summary="Apply loyalty conditions")
def apply_terms(order_id: str, payload: CustomerTermsRequest) -> dict:
    order = get_order(order_id)
    try:
        return orders.apply_customer_terms(store, order, payload.loyaltyNumber)
    except ValueError as exc:
        raise translate(exc, order) from exc


@app.post("/orders/{order_id}/reservations", status_code=201, operation_id="reserveOrder", summary="Reserve all order items")
def reserve_order(order_id: str, payload: ReserveOrderRequest) -> dict:
    order = get_order(order_id)
    try:
        return inventory.reserve_order(store, order, payload.holdMinutes)
    except ValueError as exc:
        raise translate(exc, order) from exc


@app.post("/orders/{order_id}/quote", operation_id="calculateOrderPrice", summary="Calculate authoritative order total")
def quote_order(order_id: str) -> dict:
    order = get_order(order_id)
    try:
        return orders.price_order(order)
    except ValueError as exc:
        raise translate(exc, order) from exc


@app.post("/orders/{order_id}/payments", status_code=201, operation_id="payOrder", summary="Capture order payment")
def pay_order(order_id: str, payload: PaymentRequest) -> dict:
    order = get_order(order_id)
    try:
        return payments.pay_order(store, order, payload, PAYMENT_TOKEN)
    except ValueError as exc:
        raise translate(exc, order) from exc


@app.post("/orders/{order_id}/shipments", status_code=201, operation_id="createShipment", summary="Prepare a paid order for delivery")
def create_shipment(order_id: str, payload: CreateShipmentRequest) -> dict:
    order = get_order(order_id)
    try:
        return shipping.create_shipment(store, order, payload.delivery.model_dump())
    except ValueError as exc:
        raise translate(exc, order) from exc


@app.get("/shipments/{shipment_id}", operation_id="getShipment", summary="Get shipment and delivery details")
def get_shipment(shipment_id: str) -> dict:
    try:
        return shipping.require_shipment(store, shipment_id)
    except KeyError as exc:
        raise translate(exc) from exc


@app.patch("/shipments/{shipment_id}/delivery", operation_id="updateShipmentDelivery", summary="Change address and delivery window")
def update_shipment(shipment_id: str, payload: UpdateDeliveryRequest) -> dict:
    shipment = get_shipment(shipment_id)
    try:
        return shipping.update_delivery(shipment, payload.model_dump())
    except ValueError as exc:
        raise translate(exc, shipment) from exc


@app.post("/shipments/{shipment_id}/handoff", operation_id="handoffShipment", summary="Assign and hand shipment to courier")
def handoff_shipment(shipment_id: str, payload: CourierHandoffRequest) -> dict:
    shipment = get_shipment(shipment_id)
    if payload.courierId != VALID_COURIER:
        raise error(404, "COURIER_NOT_FOUND", "Courier was not found.", field="courierId")
    try:
        return shipping.handoff(store, shipment, payload.courierId)
    except ValueError as exc:
        raise translate(exc, shipment) from exc


@app.post("/shipments/{shipment_id}/deliveries/start", status_code=201, operation_id="startDelivery", summary="Start courier delivery")
def start_delivery(shipment_id: str, payload: StartDeliveryRequest) -> dict:
    shipment = get_shipment(shipment_id)
    try:
        return shipping.start_delivery(store, shipment, payload.courierId, payload.startedAt)
    except ValueError as exc:
        raise translate(exc, shipment) from exc


@app.post("/deliveries/{delivery_id}/complete", operation_id="completeDelivery", summary="Confirm recipient handover")
def complete_delivery(delivery_id: str, payload: CompleteDeliveryRequest) -> dict:
    delivery = store.deliveries.get(delivery_id)
    if not delivery:
        raise error(404, "DELIVERY_NOT_FOUND", "Delivery was not found.")
    try:
        return shipping.complete_delivery(
            store,
            delivery,
            payload.courierId,
            payload.recipientCode,
            payload.deliveredAt,
        )
    except ValueError as exc:
        raise translate(exc, delivery) from exc


@app.post("/orders/{order_id}/returns", status_code=201, operation_id="createReturn", summary="Register a partial order return")
def create_return(order_id: str, payload: CreateReturnRequest) -> dict:
    order = get_order(order_id)
    try:
        return returns.create_return(
            store,
            order,
            [item.model_dump() for item in payload.items],
            payload.reason,
        )
    except (KeyError, ValueError) as exc:
        raise translate(exc, order) from exc


@app.post("/returns/{return_id}/receive", operation_id="receiveReturn", summary="Receive returned goods into warehouse stock")
def receive_return(return_id: str, payload: ReceiveReturnRequest) -> dict:
    try:
        result = returns.require_return(store, return_id)
        return returns.receive_return(store, result, payload.warehouseId, payload.receivedAt)
    except (KeyError, ValueError) as exc:
        raise translate(exc, locals().get("result")) from exc


@app.post("/returns/{return_id}/refund", operation_id="refundReturn", summary="Refund the accepted returned quantity")
def refund_return(return_id: str, payload: RefundReturnRequest) -> dict:
    try:
        result = returns.require_return(store, return_id)
        return returns.refund_return(store, result, payload.paymentToken, PAYMENT_TOKEN)
    except (KeyError, ValueError) as exc:
        raise translate(exc, locals().get("result")) from exc


@app.post("/mock/reset", operation_id="resetMockState", summary="Restore deterministic initial state")
def reset_mock() -> dict:
    store.reset()
    return {"status": "RESET", "resetAt": store.lastResetAt}


@app.get("/mock/state", include_in_schema=False)
def mock_state() -> dict:
    return {
        "inventory": {f"{key[0]}:{key[1]}": value for key, value in store.inventory.items()},
        "orders": store.orders,
        "reservations": store.reservations,
        "payments": store.payments,
        "shipments": store.shipments,
        "deliveries": store.deliveries,
        "returns": store.returns,
    }
