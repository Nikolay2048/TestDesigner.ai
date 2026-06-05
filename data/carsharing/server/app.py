from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field


app = FastAPI(title="Carsharing Mock API", version="1.0.0")


LOCATIONS = [
    {"id": "LOC-CITY-1", "city": "Moscow", "airport": False, "openingHours": "08:00-22:00"},
    {"id": "LOC-SVO", "city": "Moscow", "airport": True, "openingHours": "00:00-24:00"},
]

VEHICLES = [
    {
        "id": "CAR-EC-1",
        "category": "ECONOMY",
        "transmission": "AUTO",
        "fuelPolicy": "FULL_TO_FULL",
        "dailyRate": 3900.0,
        "deposit": 15000.0,
    },
    {
        "id": "CAR-SUV-1",
        "category": "SUV",
        "transmission": "AUTO",
        "fuelPolicy": "FULL_TO_FULL",
        "dailyRate": 7200.0,
        "deposit": 30000.0,
    },
]


class SearchVehiclesRequest(BaseModel):
    pickupLocationId: str
    returnLocationId: str
    pickupDate: date
    returnDate: date
    driverAge: int = Field(ge=18)
    category: Literal["ECONOMY", "COMPACT", "SUV", "VAN"] | None = None


class Customer(BaseModel):
    firstName: str
    lastName: str
    phone: str
    email: str
    driverLicenseNo: str


class ReservationCreateRequest(BaseModel):
    vehicleId: str
    pickupLocationId: str
    returnLocationId: str
    pickupDate: date
    returnDate: date
    customer: Customer


class ExtrasRequest(BaseModel):
    extras: list[str]


class LoyaltyValidateRequest(BaseModel):
    reservationId: str
    loyaltyNumber: str


class PreauthRequest(BaseModel):
    reservationId: str
    cardToken: str
    amount: float


class PickupRequest(BaseModel):
    odometer: int = Field(ge=0)
    fuelLevelPercent: int = Field(ge=0, le=100)
    damageConfirmed: bool


class ExtendRequest(BaseModel):
    newReturnDate: date


class ReturnRequest(BaseModel):
    odometer: int
    fuelLevelPercent: int


class IncidentRequest(BaseModel):
    rentalId: str
    type: Literal["SCRATCH", "ACCIDENT", "LOST_KEY"]
    description: str


def _initial_state() -> dict[str, Any]:
    return {
        "reservations": {},
        "rentals": {},
        "payments": {},
        "incidents": {},
        "reservation_counter": 1,
        "rental_counter": 1,
        "payment_counter": 1,
        "incident_counter": 1,
    }


state = _initial_state()


@app.post("/mock/reset")
def reset_mock() -> dict[str, str]:
    state.clear()
    state.update(_initial_state())
    return {"status": "reset"}


@app.get("/locations")
def list_locations() -> dict[str, Any]:
    return {"locations": deepcopy(LOCATIONS)}


@app.post("/vehicles/search")
def search_vehicles(payload: SearchVehiclesRequest) -> dict[str, Any]:
    if payload.returnDate <= payload.pickupDate:
        raise business_error(
            400,
            "INVALID_DATES",
            "returnDate must be after pickupDate.",
            "Use a rental period of at least one day.",
        )
    if payload.category == "SUV" and payload.driverAge < 30:
        raise business_error(
            400,
            "DRIVER_TOO_YOUNG_FOR_SUV",
            "SUV rentals require a driver age of at least 30.",
            "Use driverAge=30 for SUV rentals.",
        )
    vehicles = [
        deepcopy(item)
        for item in VEHICLES
        if payload.category is None or item["category"] == payload.category
    ]
    return {"vehicles": vehicles}


@app.get("/vehicles/{vehicleId}")
def get_vehicle(vehicleId: str) -> dict[str, Any]:
    vehicle = _vehicle(vehicleId)
    return deepcopy(vehicle)


@app.post("/reservations", status_code=status.HTTP_201_CREATED)
def create_reservation(payload: ReservationCreateRequest) -> dict[str, Any]:
    vehicle = _vehicle(payload.vehicleId)
    rental_days = max(1, (payload.returnDate - payload.pickupDate).days)
    one_way_fee = 3500.0 if payload.pickupLocationId != payload.returnLocationId else 0.0
    total = vehicle["dailyRate"] * rental_days + one_way_fee
    reservation_id = f"RSV-{state['reservation_counter']:04d}"
    state["reservation_counter"] += 1
    reservation = {
        "id": reservation_id,
        "status": "DRAFT",
        "vehicleId": payload.vehicleId,
        "pickupLocationId": payload.pickupLocationId,
        "returnLocationId": payload.returnLocationId,
        "pickupDate": payload.pickupDate.isoformat(),
        "returnDate": payload.returnDate.isoformat(),
        "customer": payload.customer.model_dump(),
        "totalAmount": total,
        "depositAmount": vehicle["deposit"],
        "paymentRequired": total,
        "loyaltyDiscountApplied": False,
        "extras": [],
    }
    state["reservations"][reservation_id] = reservation
    return deepcopy(reservation)


@app.get("/reservations/{reservationId}")
def get_reservation(reservationId: str) -> dict[str, Any]:
    return deepcopy(_reservation(reservationId))


@app.post("/reservations/{reservationId}/extras")
def add_extras(reservationId: str, payload: ExtrasRequest) -> dict[str, Any]:
    reservation = _reservation(reservationId)
    reservation["extras"] = list(payload.extras)
    reservation["totalAmount"] += 1200.0 * len(payload.extras)
    reservation["paymentRequired"] = reservation["totalAmount"]
    return deepcopy(reservation)


@app.post("/loyalty/validate")
def validate_loyalty(payload: LoyaltyValidateRequest) -> dict[str, Any]:
    reservation = _reservation(payload.reservationId)
    if payload.loyaltyNumber != "LOYAL-GOLD-777":
        raise business_error(
            400,
            "LOYALTY_NOT_FOUND",
            "Loyalty number was not found.",
            "Use loyaltyNumber=LOYAL-GOLD-777.",
        )
    if not reservation["loyaltyDiscountApplied"]:
        reservation["loyaltyDiscountApplied"] = True
        reservation["totalAmount"] = round(reservation["totalAmount"] * 0.9, 2)
        reservation["paymentRequired"] = reservation["totalAmount"]
    return {"valid": True, "level": "GOLD", "discountPercent": 10}


@app.post("/payments/preauth")
def preauth(payload: PreauthRequest) -> dict[str, Any]:
    reservation = _reservation(payload.reservationId)
    required = float(reservation["paymentRequired"])
    if round(payload.amount, 2) != round(required, 2):
        raise business_error(
            400,
            "INVALID_PREAUTH_AMOUNT",
            "Preauthorization amount does not match current payable amount.",
            f"Use amount={required:g}.",
        )
    payment_id = f"PAY-{state['payment_counter']:04d}"
    state["payment_counter"] += 1
    reservation["status"] = "CONFIRMED"
    state["payments"][payment_id] = {
        "paymentId": payment_id,
        "reservationId": payload.reservationId,
        "authorizedAmount": required,
        "status": "AUTHORIZED",
    }
    return {"paymentId": payment_id, "status": "AUTHORIZED", "authorizedAmount": required}


@app.post("/rentals/{reservationId}/pickup")
def pickup(reservationId: str, payload: PickupRequest) -> dict[str, Any]:
    reservation = _reservation(reservationId)
    if reservation["status"] != "CONFIRMED":
        raise business_error(
            409,
            "RESERVATION_NOT_CONFIRMED",
            "Reservation must be confirmed before pickup.",
            "Run POST /payments/preauth first.",
        )
    if payload.fuelLevelPercent != 100:
        raise business_error(
            400,
            "FUEL_LEVEL_NOT_FULL",
            "Vehicle must be picked up with a full tank.",
            "Use fuelLevelPercent=100.",
        )
    rental_id = f"RNT-{state['rental_counter']:04d}"
    state["rental_counter"] += 1
    rental = {
        "rentalId": rental_id,
        "reservationId": reservationId,
        "status": "ACTIVE",
        "returnDate": reservation["returnDate"],
        "odometerStart": payload.odometer,
        "incidentBeforeClose": False,
    }
    state["rentals"][rental_id] = rental
    return {"rentalId": rental_id, "status": "ACTIVE"}


@app.get("/rentals/{rentalId}")
def get_rental(rentalId: str) -> dict[str, Any]:
    return deepcopy(_rental(rentalId))


@app.post("/rentals/{rentalId}/extend")
def extend_rental(rentalId: str, payload: ExtendRequest) -> dict[str, Any]:
    rental = _rental(rentalId)
    current_return = date.fromisoformat(rental["returnDate"])
    if payload.newReturnDate <= current_return:
        next_date = current_return + timedelta(days=1)
        raise business_error(
            409,
            "INVALID_EXTENSION_DATE",
            "New return date must be after current return date.",
            f"Use newReturnDate={next_date.isoformat()}.",
        )
    rental["returnDate"] = payload.newReturnDate.isoformat()
    return {"rentalId": rentalId, "newReturnDate": rental["returnDate"], "extraAmount": 3900.0}


@app.post("/incidents", status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentRequest) -> dict[str, Any]:
    rental = _rental(payload.rentalId)
    if rental["status"] != "ACTIVE":
        raise business_error(
            400,
            "RENTAL_ALREADY_CLOSED",
            "Incident cannot be registered after rental is closed.",
            "Register incident before POST /rentals/{rentalId}/return.",
        )
    incident_id = f"INC-{state['incident_counter']:04d}"
    state["incident_counter"] += 1
    rental["incidentBeforeClose"] = True
    state["incidents"][incident_id] = {
        "incidentId": incident_id,
        "rentalId": payload.rentalId,
        "type": payload.type,
        "description": payload.description,
        "status": "OPEN",
    }
    return {"incidentId": incident_id, "status": "OPEN"}


@app.post("/rentals/{rentalId}/return")
def return_rental(rentalId: str, payload: ReturnRequest) -> dict[str, Any]:
    rental = _rental(rentalId)
    if rental["status"] != "ACTIVE":
        raise business_error(
            409,
            "RENTAL_ALREADY_CLOSED",
            "Rental is already closed.",
            "Use an ACTIVE rental.",
        )
    if payload.fuelLevelPercent != 100:
        raise business_error(
            400,
            "FUEL_LEVEL_NOT_FULL",
            "Vehicle must be returned with a full tank.",
            "Use fuelLevelPercent=100.",
        )
    rental["status"] = "CLOSED"
    if rental.get("incidentBeforeClose"):
        return {"status": "NEEDS_INSPECTION", "extraCharge": 0.0}
    return {"status": "CLOSED", "extraCharge": 0.0}


def _vehicle(vehicle_id: str) -> dict[str, Any]:
    for vehicle in VEHICLES:
        if vehicle["id"] == vehicle_id:
            return vehicle
    raise business_error(404, "VEHICLE_NOT_FOUND", "Vehicle was not found.", "Use id from POST /vehicles/search.")


def _reservation(reservation_id: str) -> dict[str, Any]:
    reservation = state["reservations"].get(reservation_id)
    if not reservation:
        raise business_error(404, "RESERVATION_NOT_FOUND", "Reservation was not found.", "Create a reservation first and reuse id from the response.")
    return reservation


def _rental(rental_id: str) -> dict[str, Any]:
    rental = state["rentals"].get(rental_id)
    if not rental:
        raise business_error(404, "RENTAL_NOT_FOUND", "Rental was not found.", "Start rental first and reuse rentalId from the response.")
    return rental


def business_error(status_code: int, code: str, message: str, hint: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, "hint": hint})
