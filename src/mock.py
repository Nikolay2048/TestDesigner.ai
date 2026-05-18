"""Local REST mock server for smoke tests."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Flask, jsonify, request

app = Flask(__name__)

users: dict[str, dict] = {}
vehicles: dict[str, dict] = {}
bookings: dict[str, dict] = {}
booking_counter: int = 1
accidents: dict[str, dict] = {}
accident_counter: int = 1
participant_counter: int = 1
claim_counter: int = 1
assessment_counter: int = 1
payment_counter: int = 1


def reset_state() -> None:
    global users, vehicles, bookings, booking_counter
    global accidents, accident_counter, participant_counter, claim_counter, assessment_counter, payment_counter
    users = {
        "user-1": {"userId": "user-1", "status": "ACTIVE"},
        "user-blocked": {"userId": "user-blocked", "status": "BLOCKED"},
    }
    vehicles = {
        "vehicle-1": {"vehicleId": "vehicle-1", "brand": "Toyota", "model": "Camry", "status": "AVAILABLE"},
        "vehicle-2": {"vehicleId": "vehicle-2", "brand": "BMW", "model": "X5", "status": "AVAILABLE"},
    }
    bookings = {}
    booking_counter = 1
    accidents = {}
    accident_counter = 1
    participant_counter = 1
    claim_counter = 1
    assessment_counter = 1
    payment_counter = 1


reset_state()


def error(status: int, code: str, message: str):
    return jsonify({"code": code, "message": message}), status


@app.get("/health")
def health():
    return jsonify({"status": "UP"})


@app.get("/v1/vehicles/available")
def available_vehicles():
    items = [vehicle for vehicle in vehicles.values() if vehicle["status"] == "AVAILABLE"]
    return jsonify({"count": len(items), "items": items})


@app.post("/v1/bookings")
def create_booking():
    global booking_counter
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    vehicle_id = body.get("vehicleId")
    start_date = body.get("startDate")
    if not user_id:
        return error(400, "USER_ID_REQUIRED", "userId is required")
    if not vehicle_id:
        return error(400, "VEHICLE_ID_REQUIRED", "vehicleId is required")
    if not start_date:
        return error(400, "START_DATE_REQUIRED", "startDate is required")
    if user_id not in users:
        return error(404, "USER_NOT_FOUND", "User not found")
    if users[user_id]["status"] == "BLOCKED":
        return error(403, "USER_BLOCKED", "User is blocked")
    if vehicle_id not in vehicles:
        return error(404, "VEHICLE_NOT_FOUND", "Vehicle not found")
    if vehicles[vehicle_id]["status"] != "AVAILABLE":
        return error(409, "VEHICLE_NOT_AVAILABLE", "Vehicle is not available")
    if not _future(start_date):
        return error(400, "START_DATE_MUST_BE_IN_FUTURE", "startDate must be in the future")
    booking_id = f"booking-{booking_counter}"
    booking_counter += 1
    booking = {
        "bookingId": booking_id,
        "userId": user_id,
        "vehicleId": vehicle_id,
        "startDate": start_date,
        "status": "CREATED",
    }
    bookings[booking_id] = booking
    vehicles[vehicle_id]["status"] = "BOOKED"
    return jsonify(booking), 201


@app.get("/v1/bookings/<booking_id>")
def get_booking(booking_id: str):
    if booking_id not in bookings:
        return error(404, "BOOKING_NOT_FOUND", "Booking not found")
    return jsonify(bookings[booking_id])


@app.post("/v1/bookings/<booking_id>/cancel")
def cancel_booking(booking_id: str):
    if booking_id not in bookings:
        return error(404, "BOOKING_NOT_FOUND", "Booking not found")
    booking = bookings[booking_id]
    if booking["status"] == "CANCELLED":
        return error(409, "BOOKING_ALREADY_CANCELLED", "Booking already cancelled")
    booking["status"] = "CANCELLED"
    if booking["vehicleId"] in vehicles:
        vehicles[booking["vehicleId"]]["status"] = "AVAILABLE"
    return jsonify(booking)


def _future(value: str) -> bool:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except ValueError:
        return False


@app.post("/v1/accidents")
def create_accident():
    global accident_counter
    body = request.get_json(silent=True) or {}
    if not body.get("date"):
        return error(400, "DATE_REQUIRED", "date is required")
    if not body.get("location"):
        return error(400, "LOCATION_REQUIRED", "location is required")
    accident_id = f"accident-{accident_counter}"
    accident_counter += 1
    accident = {
        "accidentId": accident_id,
        "date": body.get("date"),
        "location": body.get("location"),
        "description": body.get("description"),
        "status": "REGISTERED",
        "participantsCount": 0,
        "totalDamageAmount": 0.0,
        "_participants": {},
        "_claims": {},
    }
    accidents[accident_id] = accident
    return jsonify(_public(accident)), 201


@app.get("/v1/accidents/<accident_id>")
def get_accident(accident_id: str):
    if accident_id not in accidents:
        return error(404, "ACCIDENT_NOT_FOUND", "Accident not found")
    return jsonify(_public(accidents[accident_id]))


@app.post("/v1/accidents/<accident_id>/participants")
def create_participant(accident_id: str):
    global participant_counter
    if accident_id not in accidents:
        return error(404, "ACCIDENT_NOT_FOUND", "Accident not found")
    body = request.get_json(silent=True) or {}
    participant_name = body.get("name") or body.get("fullName")
    participant_phone = body.get("phone") or "+79990000000"
    if not body.get("role"):
        return error(400, "ROLE_REQUIRED", "role is required")
    if not participant_name:
        return error(400, "NAME_REQUIRED", "name is required")
    participant_id = f"participant-{participant_counter}"
    participant_counter += 1
    participant = {
        "participantId": participant_id,
        "accidentId": accident_id,
        "role": body.get("role"),
        "name": participant_name,
        "phone": participant_phone,
        "insurancePolicy": body.get("insurancePolicy"),
        "faultPercentage": body.get("faultPercentage", body.get("faultShare", 0)),
        "_vehicles": {},
    }
    accident = accidents[accident_id]
    accident["_participants"][participant_id] = participant
    accident["participantsCount"] = len(accident["_participants"])
    return jsonify(_public(participant)), 201


@app.post("/v1/accidents/<accident_id>/participants/<participant_id>/vehicles")
def create_participant_vehicle(accident_id: str, participant_id: str):
    if accident_id not in accidents:
        return error(404, "ACCIDENT_NOT_FOUND", "Accident not found")
    participant = accidents[accident_id]["_participants"].get(participant_id)
    if not participant:
        return error(404, "PARTICIPANT_NOT_FOUND", "Participant not found")
    body = request.get_json(silent=True) or {}
    vehicle_id = f"accident-vehicle-{len(participant['_vehicles']) + 1}"
    vehicle = {
        "accidentVehicleId": vehicle_id,
        "participantId": participant_id,
        "plateNumber": body.get("plateNumber", "A001AA"),
        "brand": body.get("brand", "Toyota"),
        "model": body.get("model", "Camry"),
        "damageAmount": body.get("damageAmount", 0.0),
    }
    participant["_vehicles"][vehicle_id] = vehicle
    _recalculate_damage(accidents[accident_id])
    return jsonify(vehicle), 201


@app.post("/v1/accidents/<accident_id>/claims")
def create_claim(accident_id: str):
    global claim_counter
    if accident_id not in accidents:
        return error(404, "ACCIDENT_NOT_FOUND", "Accident not found")
    body = request.get_json(silent=True) or {}
    claimant_id = body.get("claimantParticipantId") or _first_participant(accident_id, role="VICTIM")
    if not claimant_id:
        return error(400, "CLAIMANT_REQUIRED", "claimantParticipantId is required")
    claim_id = f"claim-{claim_counter}"
    claim_counter += 1
    claim = {
        "claimId": claim_id,
        "accidentId": accident_id,
        "claimantParticipantId": claimant_id,
        "claimedAmount": body.get("claimedAmount", 0.0),
        "approvedAmount": 0.0,
        "description": body.get("description"),
        "status": "DRAFT",
        "_assessments": {},
        "_payments": {},
    }
    accidents[accident_id]["_claims"][claim_id] = claim
    return jsonify(_public(claim)), 201


@app.get("/v1/accidents/<accident_id>/claims/<claim_id>")
def get_claim(accident_id: str, claim_id: str):
    claim = _claim(accident_id, claim_id)
    if not claim:
        return error(404, "CLAIM_NOT_FOUND", "Claim not found")
    return jsonify(_public(claim))


@app.patch("/v1/accidents/<accident_id>/claims/<claim_id>")
def update_claim(accident_id: str, claim_id: str):
    claim = _claim(accident_id, claim_id)
    if not claim:
        return error(404, "CLAIM_NOT_FOUND", "Claim not found")
    body = request.get_json(silent=True) or {}
    for field in ("claimedAmount", "description"):
        if field in body:
            claim[field] = body[field]
    return jsonify(_public(claim))


@app.post("/v1/accidents/<accident_id>/claims/<claim_id>/submit")
def submit_claim(accident_id: str, claim_id: str):
    claim = _claim(accident_id, claim_id)
    if not claim:
        return error(404, "CLAIM_NOT_FOUND", "Claim not found")
    if not claim.get("claimedAmount") or claim["claimedAmount"] <= 0:
        return error(400, "CLAIM_AMOUNT_REQUIRED", "claimedAmount must be greater than 0")
    claim["status"] = "SUBMITTED"
    return jsonify(_public(claim))


@app.post("/v1/accidents/<accident_id>/claims/<claim_id>/assessments")
def create_assessment(accident_id: str, claim_id: str):
    global assessment_counter
    claim = _claim(accident_id, claim_id)
    if not claim:
        return error(404, "CLAIM_NOT_FOUND", "Claim not found")
    body = request.get_json(silent=True) or {}
    assessment_id = f"assessment-{assessment_counter}"
    assessment_counter += 1
    assessment = {
        "assessmentId": assessment_id,
        "claimId": claim_id,
        "expertName": body.get("expertName", "Expert"),
        "scheduledDate": body.get("scheduledDate", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        "assessedAmount": body.get("assessedAmount", 0.0),
        "report": body.get("report"),
        "status": "SCHEDULED",
    }
    claim["_assessments"][assessment_id] = assessment
    return jsonify(assessment), 201


@app.patch("/v1/accidents/<accident_id>/claims/<claim_id>/assessments/<assessment_id>")
def update_assessment(accident_id: str, claim_id: str, assessment_id: str):
    claim = _claim(accident_id, claim_id)
    if not claim or assessment_id not in claim["_assessments"]:
        return error(404, "ASSESSMENT_NOT_FOUND", "Assessment not found")
    body = request.get_json(silent=True) or {}
    assessment = claim["_assessments"][assessment_id]
    for field in ("assessedAmount", "report", "status"):
        if field in body:
            assessment[field] = body[field]
    if assessment.get("status") == "COMPLETED":
        claim["status"] = "UNDER_REVIEW"
        claim["approvedAmount"] = assessment.get("assessedAmount") or claim.get("claimedAmount") or 0.0
    return jsonify(assessment)


@app.post("/v1/accidents/<accident_id>/claims/<claim_id>/payments")
def create_payment(accident_id: str, claim_id: str):
    global payment_counter
    claim = _claim(accident_id, claim_id)
    if not claim:
        return error(404, "CLAIM_NOT_FOUND", "Claim not found")
    for payment in claim["_payments"].values():
        if payment["status"] in ("PENDING", "CONFIRMED"):
            return error(409, "PAYMENT_ALREADY_EXISTS", "Active payment already exists")
    if claim["status"] not in ("UNDER_REVIEW", "APPROVED", "PAID"):
        return error(409, "CLAIM_NOT_PAYABLE", "Claim is not payable")
    body = request.get_json(silent=True) or {}
    amount = body.get("amount") or claim.get("approvedAmount") or claim.get("claimedAmount") or 1000.0
    payment_id = f"payment-{payment_counter}"
    payment_counter += 1
    payment = {
        "paymentId": payment_id,
        "claimId": claim_id,
        "claimantParticipantId": claim["claimantParticipantId"],
        "amount": amount,
        "status": "PENDING",
        "confirmedAt": None,
    }
    claim["_payments"][payment_id] = payment
    return jsonify(payment), 201


@app.post("/v1/accidents/<accident_id>/claims/<claim_id>/payments/<payment_id>/confirm")
def confirm_payment(accident_id: str, claim_id: str, payment_id: str):
    claim = _claim(accident_id, claim_id)
    if not claim or payment_id not in claim["_payments"]:
        return error(404, "PAYMENT_NOT_FOUND", "Payment not found")
    payment = claim["_payments"][payment_id]
    if payment["status"] != "PENDING":
        return error(409, "PAYMENT_NOT_PENDING", "Payment is not pending")
    payment["status"] = "CONFIRMED"
    payment["confirmedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    claim["status"] = "PAID"
    return jsonify(payment)


def _public(value: dict) -> dict:
    return {k: v for k, v in value.items() if not k.startswith("_")}


def _claim(accident_id: str, claim_id: str):
    if accident_id not in accidents:
        return None
    return accidents[accident_id]["_claims"].get(claim_id)


def _first_participant(accident_id: str, role: str | None = None):
    participants = accidents[accident_id]["_participants"].values()
    for participant in participants:
        if role is None or participant.get("role") == role:
            return participant["participantId"]
    return None


def _recalculate_damage(accident: dict) -> None:
    total = 0.0
    for participant in accident["_participants"].values():
        for vehicle in participant["_vehicles"].values():
            total += float(vehicle.get("damageAmount") or 0)
    accident["totalDamageAmount"] = total


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
