from datetime import date, datetime, timedelta
from typing import Dict, Any, List
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, Field

app = FastAPI(title="Synthetic Car Rental Mock Server", version="1.0.0")

LOCATIONS = [
    {"id": "LOC-CITY-1", "city": "Moscow", "airport": False, "openingHours": "08:00-22:00"},
    {"id": "LOC-AIR-1", "city": "Moscow", "airport": True, "openingHours": "06:00-23:30"},
]

VEHICLES = [
    {"id": "CAR-EC-1", "category": "ECONOMY", "transmission": "AUTO", "fuelPolicy": "FULL_TO_FULL", "dailyRate": 3900.0, "deposit": 15000.0},
    {"id": "CAR-SUV-1", "category": "SUV", "transmission": "AUTO", "fuelPolicy": "FULL_TO_FULL", "dailyRate": 7900.0, "deposit": 30000.0},
]

reservations: Dict[str, Dict[str, Any]] = {}
rentals: Dict[str, Dict[str, Any]] = {}
incidents: Dict[str, Dict[str, Any]] = {}


def vehicle_by_id(vehicle_id: str) -> Dict[str, Any]:
    for v in VEHICLES:
        if v["id"] == vehicle_id:
            return v
    raise HTTPException(status_code=404, detail={"code": "VEHICLE_NOT_FOUND", "message": "Vehicle not found"})


def error(status: int, code: str, message: str, hint: str | None = None):
    payload = {"code": code, "message": message}
    if hint:
        payload["hint"] = hint
    raise HTTPException(status_code=status, detail=payload)


def parse_date(value: str, field_name: str = "date") -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        error(
            400,
            "INVALID_DATE_FORMAT",
            f"{field_name} must use YYYY-MM-DD format",
            f"Use {field_name} like 2026-06-10, without time or timezone",
        )


class SearchVehiclesRequest(BaseModel):
    pickupLocationId: str
    returnLocationId: str
    pickupDate: str
    returnDate: str
    driverAge: int = Field(ge=18)
    category: str | None = None


class Customer(BaseModel):
    firstName: str
    lastName: str
    phone: str
    email: EmailStr
    driverLicenseNo: str


class ReservationCreateRequest(BaseModel):
    vehicleId: str
    pickupLocationId: str
    returnLocationId: str
    pickupDate: str
    returnDate: str
    customer: Customer


class ExtrasRequest(BaseModel):
    extras: List[str]


class LoyaltyValidateRequest(BaseModel):
    loyaltyNumber: str
    reservationId: str


class PreauthRequest(BaseModel):
    reservationId: str
    cardToken: str
    amount: float


class PickupRequest(BaseModel):
    odometer: int
    fuelLevelPercent: int
    damageConfirmed: bool


class ExtendRequest(BaseModel):
    newReturnDate: str


class ReturnRequest(BaseModel):
    odometer: int
    fuelLevelPercent: int


class IncidentRequest(BaseModel):
    rentalId: str
    type: str
    description: str


@app.get("/locations")
def locations():
    return {"locations": LOCATIONS}


@app.get("/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: str):
    return vehicle_by_id(vehicle_id)


@app.post("/vehicles/search")
def search_vehicles(req: SearchVehiclesRequest):
    pickup = parse_date(req.pickupDate, "pickupDate")
    ret = parse_date(req.returnDate, "returnDate")
    if ret <= pickup:
        error(400, "INVALID_DATES", "returnDate must be later than pickupDate", "Set returnDate to at least pickupDate + 1 day")
    if req.category == "SUV" and req.driverAge < 30:
        error(400, "DRIVER_TOO_YOUNG_FOR_SUV", "SUV rental requires driver age 30+", "Use driverAge=30 for SUV happy path")
    result = [v for v in VEHICLES if req.category in (None, v["category"])]
    return {"vehicles": result}


@app.post("/reservations", status_code=201)
def create_reservation(req: ReservationCreateRequest):
    vehicle = vehicle_by_id(req.vehicleId)
    pickup = parse_date(req.pickupDate, "pickupDate")
    ret = parse_date(req.returnDate, "returnDate")
    if ret <= pickup:
        error(400, "INVALID_DATES", "returnDate must be after pickupDate", "Use a rental period of at least one day")
    days = (ret - pickup).days
    total = vehicle["dailyRate"] * days
    if req.pickupLocationId != req.returnLocationId:
        total += 2500.0
    rid = "RSV-" + uuid4().hex[:8].upper()
    res = {
        "id": rid,
        "status": "DRAFT",  # intentional server behavior: OpenAPI allows CONFIRMED, analyst may expect after creation
        "vehicleId": vehicle["id"],
        "pickupLocationId": req.pickupLocationId,
        "returnLocationId": req.returnLocationId,
        "pickupDate": req.pickupDate,
        "returnDate": req.returnDate,
        "totalAmount": total,
        "depositAmount": vehicle["deposit"],
        "paymentRequired": total + vehicle["deposit"],
        "loyaltyDiscountApplied": False,
        "extras": [],
        "paymentAuthorized": False,
    }
    reservations[rid] = res
    return res


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str):
    res = reservations.get(reservation_id)
    if not res:
        error(404, "RESERVATION_NOT_FOUND", "Reservation not found")
    return res


@app.post("/reservations/{reservation_id}/extras")
def add_extras(reservation_id: str, req: ExtrasRequest):
    res = reservations.get(reservation_id)
    if not res:
        error(404, "RESERVATION_NOT_FOUND", "Reservation not found")
    if res["paymentAuthorized"]:
        error(409, "PAYMENT_ALREADY_AUTHORIZED", "Extras cannot be added after payment", "Add extras before /payments/preauth")
    allowed = {"CHILD_SEAT": 700.0, "ADDITIONAL_DRIVER": 1200.0, "GPS": 500.0, "WINTER_TIRES": 900.0}
    for item in req.extras:
        if item not in allowed:
            error(400, "UNKNOWN_EXTRA", f"Unsupported extra: {item}", "Use CHILD_SEAT, ADDITIONAL_DRIVER, GPS or WINTER_TIRES")
    res["extras"] = req.extras
    res["totalAmount"] += sum(allowed[x] for x in req.extras)
    res["paymentRequired"] = res["totalAmount"] + res["depositAmount"]
    return res


@app.post("/loyalty/validate")
def loyalty(req: LoyaltyValidateRequest):
    res = reservations.get(req.reservationId)
    if not res:
        error(404, "RESERVATION_NOT_FOUND", "Reservation not found")
    if req.loyaltyNumber != "LOYAL-GOLD-777":
        error(400, "LOYALTY_NOT_FOUND", "Loyalty number is unknown", "Use loyaltyNumber=LOYAL-GOLD-777")
    if res["paymentAuthorized"]:
        error(409, "PAYMENT_ALREADY_AUTHORIZED", "Discount cannot be applied after payment", "Validate loyalty before payment")
    res["totalAmount"] = round(res["totalAmount"] * 0.9, 2)
    res["paymentRequired"] = res["totalAmount"] + res["depositAmount"]
    res["loyaltyDiscountApplied"] = True
    return {"valid": True, "level": "GOLD", "discountPercent": 10}


@app.post("/payments/preauth")
def preauth(req: PreauthRequest):
    res = reservations.get(req.reservationId)
    if not res:
        error(404, "RESERVATION_NOT_FOUND", "Reservation not found")
    if req.cardToken == "tok_declined":
        return {"paymentId": "PAY-DECLINED", "status": "DECLINED", "authorizedAmount": 0}
    expected = round(res["paymentRequired"], 2)
    if round(req.amount, 2) != expected:
        error(400, "INVALID_PREAUTH_AMOUNT", "Preauth amount must equal current rental total plus deposit", f"Use amount={expected}")
    res["paymentAuthorized"] = True
    res["status"] = "CONFIRMED"
    return {"paymentId": "PAY-" + uuid4().hex[:8].upper(), "status": "AUTHORIZED", "authorizedAmount": expected}


@app.post("/rentals/{reservation_id}/pickup")
def pickup(reservation_id: str, req: PickupRequest):
    res = reservations.get(reservation_id)
    if not res:
        error(404, "RESERVATION_NOT_FOUND", "Reservation not found")
    if res["status"] != "CONFIRMED" or not res["paymentAuthorized"]:
        error(409, "RESERVATION_NOT_PAID", "Reservation must be paid before pickup", "Call /payments/preauth with exact amount first")
    if req.fuelLevelPercent != 100:
        error(400, "FUEL_NOT_FULL_AT_PICKUP", "Pickup requires full tank", "Use fuelLevelPercent=100")
    rid = "RNT-" + uuid4().hex[:8].upper()
    rentals[rid] = {"rentalId": rid, "reservationId": reservation_id, "status": "ACTIVE", "returnDate": res["returnDate"], "odometerStart": req.odometer, "incidentBeforeClose": False}
    return {"rentalId": rid, "status": "ACTIVE"}


@app.get("/rentals/{rental_id}")
def get_rental(rental_id: str):
    rental = rentals.get(rental_id)
    if not rental:
        error(404, "RENTAL_NOT_FOUND", "Rental not found")
    return rental


@app.post("/rentals/{rental_id}/extend")
def extend(rental_id: str, req: ExtendRequest):
    rental = rentals.get(rental_id)
    if not rental:
        error(404, "RENTAL_NOT_FOUND", "Rental not found")
    current = parse_date(rental["returnDate"], "returnDate")
    new = parse_date(req.newReturnDate, "newReturnDate")
    if new <= current:
        error(409, "INVALID_EXTENSION_DATE", "New return date must be later than current return date", f"Use newReturnDate={(current + timedelta(days=1)).isoformat()}")
    if (new - current).days > 3:
        error(409, "EXTENSION_TOO_LONG", "Extension cannot exceed 3 days", f"Use newReturnDate={(current + timedelta(days=3)).isoformat()}")
    rental["returnDate"] = req.newReturnDate
    return {"rentalId": rental_id, "newReturnDate": req.newReturnDate, "extraAmount": 3900.0 * (new - current).days}


@app.post("/incidents", status_code=201)
def incident(req: IncidentRequest):
    rental = rentals.get(req.rentalId)
    if not rental:
        error(404, "RENTAL_NOT_FOUND", "Rental not found")
    if rental["status"] == "CLOSED":
        error(400, "RENTAL_ALREADY_CLOSED", "Incident must be registered before final return closure", "Register /incidents before /rentals/{rentalId}/return")
    iid = "INC-" + uuid4().hex[:8].upper()
    rental["incidentBeforeClose"] = True
    incidents[iid] = {"incidentId": iid, "rentalId": req.rentalId, "status": "OPEN", "type": req.type, "description": req.description}
    return {"incidentId": iid, "status": "OPEN"}


@app.post("/rentals/{rental_id}/return")
def return_rental(rental_id: str, req: ReturnRequest):
    rental = rentals.get(rental_id)
    if not rental:
        error(404, "RENTAL_NOT_FOUND", "Rental not found")
    if rental["status"] == "CLOSED":
        error(409, "RENTAL_ALREADY_CLOSED", "Rental already closed")
    if req.odometer < rental["odometerStart"]:
        error(400, "ODOMETER_ROLLBACK", "Return odometer cannot be lower than pickup odometer", f"Use odometer >= {rental['odometerStart']}")
    rental["status"] = "CLOSED"
    if rental["incidentBeforeClose"]:
        return {"status": "NEEDS_INSPECTION", "extraCharge": 0.0}
    if req.fuelLevelPercent < 100:
        return {"status": "CLOSED", "extraCharge": 1800.0}
    return {"status": "CLOSED", "extraCharge": 0.0}
