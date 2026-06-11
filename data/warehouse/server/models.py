from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class OrderStatus(str, Enum):
    DRAFT = "DRAFT"
    RESERVED = "RESERVED"
    PRICED = "PRICED"
    PAID = "PAID"
    READY_TO_SHIP = "READY_TO_SHIP"
    DELIVERED = "DELIVERED"
    PARTIALLY_RETURNED = "PARTIALLY_RETURNED"


class ShipmentStatus(str, Enum):
    PREPARED = "PREPARED"
    HANDED_TO_COURIER = "HANDED_TO_COURIER"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"


class ReturnStatus(str, Enum):
    REQUESTED = "REQUESTED"
    RECEIVED = "RECEIVED"
    REFUNDED = "REFUNDED"


class DeliveryType(str, Enum):
    STANDARD = "STANDARD"
    PRIORITY = "PRIORITY"


class Address(BaseModel):
    countryCode: str = Field(pattern=r"^[A-Z]{2}$")
    regionCode: str = Field(min_length=2, max_length=12)
    city: str = Field(min_length=2, max_length=80)
    street: str = Field(min_length=3, max_length=120)
    building: str = Field(min_length=1, max_length=20)
    postalCode: str = Field(pattern=r"^\d{6}$")


class DeliveryWindow(BaseModel):
    startsAt: datetime
    endsAt: datetime

    @model_validator(mode="after")
    def validate_order(self) -> "DeliveryWindow":
        if self.endsAt <= self.startsAt:
            raise ValueError("endsAt must be later than startsAt")
        return self


class OrderItemInput(BaseModel):
    productId: str = Field(min_length=1, max_length=30)
    quantity: int = Field(ge=1, le=100)


class CreateOrderRequest(BaseModel):
    customerId: str
    warehouseId: str
    deliveryType: DeliveryType = DeliveryType.STANDARD
    requestedDeliveryDate: date
    items: list[OrderItemInput] = Field(min_length=1, max_length=20)


class ReplaceItemsRequest(BaseModel):
    items: list[OrderItemInput] = Field(min_length=1, max_length=20)


class CustomerTermsRequest(BaseModel):
    loyaltyNumber: str = Field(pattern=r"^LOY-\d{6}$")


class ReserveOrderRequest(BaseModel):
    holdMinutes: int = Field(default=30, ge=15, le=120)


class DeliveryDetailsRequest(BaseModel):
    address: Address
    window: DeliveryWindow


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0, le=9999999999.99)
    paymentToken: str = Field(min_length=8, max_length=100)
    idempotencyKey: str = Field(min_length=8, max_length=80)


class CreateShipmentRequest(BaseModel):
    delivery: DeliveryDetailsRequest


class UpdateDeliveryRequest(BaseModel):
    address: Address
    window: DeliveryWindow


class CourierHandoffRequest(BaseModel):
    courierId: str = Field(pattern=r"^COUR-\d{4}$")


class StartDeliveryRequest(BaseModel):
    courierId: str = Field(pattern=r"^COUR-\d{4}$")
    startedAt: datetime


class CompleteDeliveryRequest(BaseModel):
    courierId: str = Field(pattern=r"^COUR-\d{4}$")
    recipientCode: str = Field(pattern=r"^\d{6}$")
    deliveredAt: datetime


class ReturnItemInput(BaseModel):
    orderItemId: str
    quantity: int = Field(ge=1, le=100)


class CreateReturnRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=300)
    items: list[ReturnItemInput] = Field(min_length=1, max_length=20)


class ReceiveReturnRequest(BaseModel):
    warehouseId: str
    receivedAt: datetime


class RefundReturnRequest(BaseModel):
    paymentToken: str = Field(min_length=8, max_length=100)


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None
    hint: str | None = None
    currentState: str | None = None
    allowedStates: list[str] | None = None


class ErrorResponse(BaseModel):
    detail: ErrorDetail


JsonObject = dict[str, Any]
