from datetime import date, datetime, timezone
from itertools import count
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field


app = FastAPI(title="Clinic Booking Mock API", version="1.0.0")

VALID_LOGIN = "qa.patient@example.test"
VALID_PASSWORD = "TestPassword123!"
VALID_PROMO_CODE = "CLINIC-TEST-15"
OLD_PROMO_CODE = "CLINIC-OLD-10"

BRANCHES = [
    {"branchId": "br-msk-central", "code": "MSK-CENTRAL", "city": "Moscow", "name": "Central Clinic"},
    {"branchId": "br-msk-north", "code": "MSK-NORTH", "city": "Moscow", "name": "North Clinic"},
]

SERVICES = [
    {"serviceId": "svc-gp", "name": "General practitioner consultation", "category": "consultation", "basePrice": 2500, "minAge": 18},
    {"serviceId": "svc-family", "name": "Family doctor consultation", "category": "consultation", "basePrice": 3200, "minAge": 18},
    {"serviceId": "svc-mri", "name": "MRI diagnostics", "category": "diagnostics", "basePrice": 9000, "minAge": 18},
]

DOCTORS = [
    {"doctorId": "doc-petrova", "fullName": "Elena Petrova", "branchId": "br-msk-central", "serviceIds": ["svc-gp", "svc-family"]},
    {"doctorId": "doc-ivanov", "fullName": "Sergey Ivanov", "branchId": "br-msk-central", "serviceIds": ["svc-mri"]},
    {"doctorId": "doc-sokolova", "fullName": "Maria Sokolova", "branchId": "br-msk-north", "serviceIds": ["svc-gp"]},
]

SEED_SLOTS = [
    {"slotId": "slot-gp-001", "branchId": "br-msk-central", "doctorId": "doc-petrova", "serviceId": "svc-gp", "startsAt": "2026-06-15T09:00:00Z", "available": True},
    {"slotId": "slot-gp-002", "branchId": "br-msk-central", "doctorId": "doc-petrova", "serviceId": "svc-gp", "startsAt": "2026-06-15T11:00:00Z", "available": True},
    {"slotId": "slot-family-001", "branchId": "br-msk-central", "doctorId": "doc-petrova", "serviceId": "svc-family", "startsAt": "2026-06-16T10:00:00Z", "available": True},
    {"slotId": "slot-mri-001", "branchId": "br-msk-central", "doctorId": "doc-ivanov", "serviceId": "svc-mri", "startsAt": "2026-06-17T12:00:00Z", "available": True},
]

ids = {
    "reservation": count(1),
    "patient": count(1),
    "payment": count(1),
    "appointment": count(1),
    "record": count(1),
}
state: dict[str, Any] = {}


class LoginRequest(BaseModel):
    login: EmailStr
    password: str = Field(min_length=8)


class Patient(BaseModel):
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    birthDate: date
    email: EmailStr
    phone: str = Field(pattern=r"^\+7\d{10}$")


class CreateReservationRequest(BaseModel):
    branchId: str
    serviceId: str
    slotId: str
    patient: Patient
    loyaltyProgramNumber: str | None = None
    promoCode: str | None = None


class CreatePaymentRequest(BaseModel):
    reservationId: str
    amount: int = Field(gt=0)
    paymentToken: str
    provider: str
    cardToken: str | None = None


class CreateAppointmentRequest(BaseModel):
    reservationId: str
    paymentId: str


class RescheduleAppointmentRequest(BaseModel):
    slotId: str


class CancelAppointmentRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


def reset_state() -> None:
    global ids, state
    ids = {
        "reservation": count(1),
        "patient": count(1),
        "payment": count(1),
        "appointment": count(1),
        "record": count(1),
    }
    state = {
        "slots": {slot["slotId"]: dict(slot) for slot in SEED_SLOTS},
        "patients": {},
        "reservations": {},
        "payments": {},
        "appointments": {},
        "records": {},
    }


reset_state()


def business_error(status_code: int, code: str, message: str, hint: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, "hint": hint})


def require_item(items: list[dict[str, Any]], key: str, value: str, code: str) -> dict[str, Any]:
    for item in items:
        if item[key] == value:
            return item
    raise business_error(404, code, f"{key} '{value}' was not found.", "Use GET /mock/seed-ids or list endpoints to select a valid identifier.")


def patient_age(birth_date: date) -> int:
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def public_slot(slot_id: str) -> dict[str, Any]:
    return dict(state["slots"][slot_id])


def make_payment_token(reservation_id: str) -> str:
    return f"paytok-{reservation_id}"


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    if payload.login != VALID_LOGIN or payload.password != VALID_PASSWORD:
        raise business_error(400, "INVALID_CREDENTIALS", "Login or password is invalid.", "Use qa.patient@example.test and TestPassword123! from test-data.yaml.")
    return {"accessToken": "token-qa-patient", "tokenType": "Bearer", "expiresIn": 3600}


@app.get("/branches")
def list_branches(city: str | None = None) -> dict[str, Any]:
    items = [branch for branch in BRANCHES if city is None or branch["city"] == city]
    return {"items": items}


@app.get("/services")
def list_services(branchId: str = Query(...)) -> dict[str, Any]:
    require_item(BRANCHES, "branchId", branchId, "BRANCH_NOT_FOUND")
    return {"items": SERVICES}


@app.get("/doctors")
def list_doctors(serviceId: str | None = None, branchId: str | None = None) -> dict[str, Any]:
    items = DOCTORS
    if branchId:
        items = [doctor for doctor in items if doctor["branchId"] == branchId]
    if serviceId:
        items = [doctor for doctor in items if serviceId in doctor["serviceIds"]]
    return {"items": items}


@app.get("/slots")
def list_slots(branchId: str = Query(...), serviceId: str = Query(...), date: str | None = None) -> dict[str, Any]:
    require_item(BRANCHES, "branchId", branchId, "BRANCH_NOT_FOUND")
    require_item(SERVICES, "serviceId", serviceId, "SERVICE_NOT_FOUND")
    items = [
        slot
        for slot in state["slots"].values()
        if slot["branchId"] == branchId and slot["serviceId"] == serviceId and slot["available"]
    ]
    if date:
        items = [slot for slot in items if slot["startsAt"].startswith(date)]
    return {"items": items}


@app.post("/reservations", status_code=201)
def create_reservation(payload: CreateReservationRequest) -> dict[str, Any]:
    branch = require_item(BRANCHES, "branchId", payload.branchId, "BRANCH_NOT_FOUND")
    service = require_item(SERVICES, "serviceId", payload.serviceId, "SERVICE_NOT_FOUND")
    slot = state["slots"].get(payload.slotId)
    if not slot:
        raise business_error(404, "SLOT_NOT_FOUND", "Slot was not found.", "Call GET /slots with branchId and serviceId and reuse a returned slotId.")
    if slot["branchId"] != branch["branchId"] or slot["serviceId"] != service["serviceId"]:
        raise business_error(422, "SLOT_SERVICE_MISMATCH", "Slot does not match requested branch or service.", "Use a slot returned by GET /slots for the same branchId and serviceId.")
    if not slot["available"]:
        raise business_error(409, "SLOT_NOT_AVAILABLE", "Slot is already reserved.", "Reset mock state or choose another slotId from GET /slots.")

    age = patient_age(payload.patient.birthDate)
    if service["serviceId"] == "svc-mri" and age < 21:
        raise business_error(422, "AGE_RESTRICTED_SERVICE", "MRI diagnostics requires patient age at least 21.", "Use a birthDate that makes the patient 21 or older for serviceId svc-mri.")

    discount = 0
    if payload.promoCode:
        if payload.promoCode == OLD_PROMO_CODE:
            raise business_error(422, "PROMO_CODE_EXPIRED", "Promo code is expired.", f"Retry with active promoCode {VALID_PROMO_CODE}.")
        if payload.promoCode != VALID_PROMO_CODE:
            raise business_error(422, "PROMO_CODE_INVALID", "Promo code is not recognized.", f"Use fixed promoCode {VALID_PROMO_CODE} from test-data.yaml.")
        if payload.loyaltyProgramNumber and not payload.loyaltyProgramNumber.startswith("LP-"):
            raise business_error(422, "LOYALTY_NUMBER_INVALID", "Loyalty number has invalid format.", "Use a loyaltyProgramNumber that starts with LP-.")
        discount = max(0, min(round(service["basePrice"] * 0.15), service["basePrice"] - 500))

    patient_id = f"pat-{next(ids['patient']):04d}"
    reservation_id = f"res-{next(ids['reservation']):04d}"
    amount = service["basePrice"]
    payment_required = amount - discount
    state["patients"][patient_id] = payload.patient.model_dump(mode="json")
    slot["available"] = False
    reservation = {
        "reservationId": reservation_id,
        "branchId": payload.branchId,
        "serviceId": payload.serviceId,
        "slotId": payload.slotId,
        "doctorId": slot["doctorId"],
        "patientId": patient_id,
        "status": "HELD",
        "amount": amount,
        "discountAmount": discount,
        "paymentRequired": payment_required,
        "paymentToken": None,
    }
    state["reservations"][reservation_id] = reservation
    return reservation


@app.get("/reservations/{reservationId}")
def get_reservation(reservationId: str) -> dict[str, Any]:
    reservation = state["reservations"].get(reservationId)
    if not reservation:
        raise business_error(404, "RESERVATION_NOT_FOUND", "Reservation was not found.", "Create a reservation first and reuse reservationId from the response.")
    return reservation


@app.post("/reservations/{reservationId}/confirm")
def confirm_reservation(reservationId: str) -> dict[str, Any]:
    reservation = get_reservation(reservationId)
    if reservation["status"] != "HELD":
        raise business_error(409, "INVALID_RESERVATION_STATUS", "Only HELD reservations can be confirmed.", "Do not confirm the same reservation twice; continue with paymentToken from the first confirmation response.")
    reservation["status"] = "CONFIRMED"
    reservation["paymentToken"] = make_payment_token(reservationId)
    return reservation


@app.post("/payments", status_code=201)
def create_payment(payload: CreatePaymentRequest) -> dict[str, Any]:
    reservation = get_reservation(payload.reservationId)
    if reservation["status"] != "CONFIRMED":
        raise business_error(409, "RESERVATION_NOT_CONFIRMED", "Reservation must be confirmed before payment.", "Call POST /reservations/{reservationId}/confirm and use paymentToken from the response.")
    if payload.paymentToken != reservation["paymentToken"]:
        raise business_error(409, "PAYMENT_TOKEN_MISMATCH", "Payment token does not match reservation.", "Use paymentToken returned by POST /reservations/{reservationId}/confirm.")
    if payload.amount != reservation["paymentRequired"]:
        raise business_error(409, "PAYMENT_AMOUNT_MISMATCH", "Payment amount does not match required amount.", "Use paymentRequired returned by reservation confirmation, not the base service amount.")
    payment_id = f"pay-{next(ids['payment']):04d}"
    payment = {
        "paymentId": payment_id,
        "reservationId": payload.reservationId,
        "amount": payload.amount,
        "status": "CAPTURED",
        "provider": payload.provider,
    }
    state["payments"][payment_id] = payment
    reservation["status"] = "PAID"
    return payment


@app.post("/appointments", status_code=201)
def create_appointment(payload: CreateAppointmentRequest) -> dict[str, Any]:
    reservation = get_reservation(payload.reservationId)
    payment = state["payments"].get(payload.paymentId)
    if not payment:
        raise business_error(404, "PAYMENT_NOT_FOUND", "Payment was not found.", "Use paymentId returned by POST /payments.")
    if payment["reservationId"] != payload.reservationId or reservation["status"] != "PAID":
        raise business_error(409, "RESERVATION_NOT_PAID", "Reservation must have a captured payment before appointment creation.", "Create payment with paymentRequired and paymentToken, then retry POST /appointments.")
    existing = [item for item in state["appointments"].values() if item["reservationId"] == payload.reservationId]
    if existing:
        return existing[0]
    slot = state["slots"][reservation["slotId"]]
    appointment_id = f"apt-{next(ids['appointment']):04d}"
    appointment = {
        "appointmentId": appointment_id,
        "reservationId": payload.reservationId,
        "paymentId": payload.paymentId,
        "branchId": reservation["branchId"],
        "serviceId": reservation["serviceId"],
        "slotId": reservation["slotId"],
        "doctorId": reservation["doctorId"],
        "patientId": reservation["patientId"],
        "amount": payment["amount"],
        "discountAmount": reservation["discountAmount"],
        "startsAt": slot["startsAt"],
        "status": "PAID",
    }
    state["appointments"][appointment_id] = appointment
    return appointment


@app.get("/appointments/{appointmentId}")
def get_appointment(appointmentId: str) -> dict[str, Any]:
    appointment = state["appointments"].get(appointmentId)
    if not appointment:
        raise business_error(404, "APPOINTMENT_NOT_FOUND", "Appointment was not found.", "Create an appointment first and reuse appointmentId from the response.")
    return appointment


@app.patch("/appointments/{appointmentId}/reschedule")
def reschedule_appointment(appointmentId: str, payload: RescheduleAppointmentRequest) -> dict[str, Any]:
    appointment = get_appointment(appointmentId)
    if appointment["status"] not in {"PAID", "RESCHEDULED"}:
        raise business_error(409, "APPOINTMENT_NOT_PAID", "Appointment must be paid or rescheduled before moving it.", "Run the complete booking flow until POST /appointments returns status PAID.")
    if payload.slotId == appointment["slotId"]:
        raise business_error(409, "SAME_SLOT_SELECTED", "New slot must differ from current slot.", "Choose another slotId from GET /slots.")
    slot = state["slots"].get(payload.slotId)
    if not slot:
        raise business_error(404, "SLOT_NOT_FOUND", "Slot was not found.", "Use GET /slots for the same branch and service.")
    if slot["serviceId"] != appointment["serviceId"] or slot["branchId"] != appointment["branchId"]:
        raise business_error(422, "SLOT_SERVICE_MISMATCH", "New slot is incompatible with appointment service or branch.", "Search slots with the appointment's serviceId and branchId.")
    if not slot["available"]:
        raise business_error(409, "SLOT_NOT_AVAILABLE", "New slot is already occupied.", "Choose an available slot from GET /slots.")
    state["slots"][appointment["slotId"]]["available"] = True
    slot["available"] = False
    appointment["slotId"] = slot["slotId"]
    appointment["doctorId"] = slot["doctorId"]
    appointment["startsAt"] = slot["startsAt"]
    appointment["status"] = "RESCHEDULED"
    return appointment


@app.post("/appointments/{appointmentId}/cancel")
def cancel_appointment(appointmentId: str, payload: CancelAppointmentRequest | None = None) -> dict[str, Any]:
    appointment = get_appointment(appointmentId)
    if appointment["status"] not in {"PAID", "RESCHEDULED"}:
        raise business_error(409, "INVALID_APPOINTMENT_STATUS", "Only paid or rescheduled appointments can be cancelled.", "Do not cancel completed appointments; use a PAID or RESCHEDULED appointment.")
    appointment["status"] = "CANCELLED"
    state["slots"][appointment["slotId"]]["available"] = True
    return appointment


@app.post("/appointments/{appointmentId}/complete")
def complete_appointment(appointmentId: str) -> dict[str, Any]:
    appointment = get_appointment(appointmentId)
    if appointment["status"] == "COMPLETED":
        return {"appointment": appointment, "record": get_record(appointmentId)}
    if appointment["status"] not in {"PAID", "RESCHEDULED"}:
        raise business_error(409, "INVALID_APPOINTMENT_STATUS", "Only paid or rescheduled appointments can be completed.", "Use an appointment with status PAID or RESCHEDULED.")
    appointment["status"] = "COMPLETED"
    record_id = f"rec-{next(ids['record']):04d}"
    record = {
        "recordId": record_id,
        "appointmentId": appointmentId,
        "doctorId": appointment["doctorId"],
        "patientId": appointment["patientId"],
        "diagnosis": "No acute pathology detected. Follow-up if symptoms persist.",
        "issuedAt": datetime.now(timezone.utc).isoformat(),
    }
    state["records"][appointmentId] = record
    return {"appointment": appointment, "record": record}


@app.get("/appointments/{appointmentId}/record")
def get_record(appointmentId: str) -> dict[str, Any]:
    get_appointment(appointmentId)
    record = state["records"].get(appointmentId)
    if not record:
        raise business_error(404, "RECORD_NOT_FOUND", "Medical record is not available before visit completion.", "Call POST /appointments/{appointmentId}/complete first.")
    return record


@app.post("/mock/reset")
def mock_reset() -> dict[str, Any]:
    reset_state()
    return {"status": "reset", "message": "Mock state was reset."}


@app.get("/mock/state")
def mock_state() -> dict[str, Any]:
    return state


@app.get("/mock/seed-ids")
def mock_seed_ids() -> dict[str, Any]:
    return {
        "branches": BRANCHES,
        "services": SERVICES,
        "doctors": DOCTORS,
        "slots": list(state["slots"].values()),
        "validPromoCode": VALID_PROMO_CODE,
        "oldPromoCode": OLD_PROMO_CODE,
    }

