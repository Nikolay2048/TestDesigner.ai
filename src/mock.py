import copy
from datetime import datetime, timezone
from flask import Flask, jsonify, request

app = Flask(__name__)

_INITIAL_USERS = {
    "user-1":       {"userId": "user-1",       "name": "Ivan Ivanov", "status": "ACTIVE"},
    "user-blocked": {"userId": "user-blocked",  "name": "Petr Petrov", "status": "BLOCKED"},
}
_INITIAL_VEHICLES = {
    "vehicle-1":       {"vehicleId": "vehicle-1",       "brand": "Toyota", "model": "Camry", "status": "AVAILABLE"},
    "vehicle-2":       {"vehicleId": "vehicle-2",       "brand": "BMW",    "model": "X5",    "status": "AVAILABLE"},
    "vehicle-3":       {"vehicleId": "vehicle-3",       "brand": "Audi",   "model": "A4",    "status": "MAINTENANCE"},
    "vehicle-blocked": {"vehicleId": "vehicle-blocked", "brand": "Ford",   "model": "Focus", "status": "BLOCKED"},
}

users = copy.deepcopy(_INITIAL_USERS)
vehicles = copy.deepcopy(_INITIAL_VEHICLES)
bookings = {}
booking_counter = 1

# DTP (ДТП) state
# accidents[accidentId] = {accidentId, date, location, description, status,
#                          participantsCount, totalDamageAmount,
#                          _participants: {participantId: {..., _vehicles: {}}},
#                          _claims: {claimId: {..., _assessments: {}, _payments: {}}}}
accidents = {}
accident_counter = 1
participant_counter = 1
acc_vehicle_counter = 1
claim_counter = 1
assessment_counter = 1
dtp_payment_counter = 1


def make_error(status_code: int, code: str, message: str):
    return jsonify({
        "code": code,
        "message": message
    }), status_code


def parse_iso_datetime(value: str):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "UP"
    }), 200


@app.route("/v1/vehicles/available", methods=["GET"])
def get_available_vehicles():
    """
    Основной шаг сценария:
    Получение списка доступных автомобилей.

    Управляемые режимы для проверки агента:
    - ?mode=empty      -> нет доступных автомобилей
    - ?mode=malformed  -> некорректная структура ответа
    - ?mode=error      -> ошибка сервера
    """

    mode = request.args.get("mode")

    if mode == "error":
        return make_error(
            500,
            "VEHICLE_SERVICE_ERROR",
            "Vehicle service is temporarily unavailable"
        )

    if mode == "empty":
        return jsonify({
            "count": 0,
            "items": []
        }), 200

    if mode == "malformed":
        return jsonify({
            "total": 1,
            "vehicles": [
                {
                    "id": "vehicle-1",
                    "state": "AVAILABLE"
                }
            ]
        }), 200

    available_vehicles = [
        vehicle
        for vehicle in vehicles.values()
        if vehicle["status"] == "AVAILABLE"
    ]

    return jsonify({
        "count": len(available_vehicles),
        "items": available_vehicles
    }), 200


@app.route("/v1/bookings", methods=["POST"])
def create_booking():
    """
    Основной шаг сценария:
    Создание бронирования автомобиля.

    Управляемые режимы для проверки агента:
    - ?mode=missing_booking_id -> успешный статус, но нет bookingId
    - ?mode=wrong_status       -> успешный статус, но неверный business status
    - ?mode=server_error       -> ошибка сервера
    """

    global booking_counter

    mode = request.args.get("mode")

    if mode == "server_error":
        return make_error(
            500,
            "BOOKING_SERVICE_ERROR",
            "Booking service is temporarily unavailable"
        )

    body = request.get_json(silent=True)

    if body is None:
        return make_error(
            400,
            "EMPTY_BODY",
            "Request body is required"
        )

    user_id = body.get("userId")
    vehicle_id = body.get("vehicleId")
    start_date = body.get("startDate")

    if not user_id:
        return make_error(
            400,
            "USER_ID_REQUIRED",
            "Field userId is required"
        )

    if not vehicle_id:
        return make_error(
            400,
            "VEHICLE_ID_REQUIRED",
            "Field vehicleId is required"
        )

    if not start_date:
        return make_error(
            400,
            "START_DATE_REQUIRED",
            "Field startDate is required"
        )

    # User validation
    if user_id not in users:
        return make_error(
            404,
            "USER_NOT_FOUND",
            f"User '{user_id}' not found"
        )

    if users[user_id]["status"] == "BLOCKED":
        return make_error(
            403,
            "USER_BLOCKED",
            f"User '{user_id}' is blocked and cannot create bookings"
        )

    if vehicle_id not in vehicles:
        return make_error(
            404,
            "VEHICLE_NOT_FOUND",
            f"Vehicle with id {vehicle_id} was not found"
        )

    vehicle = vehicles[vehicle_id]

    if vehicle["status"] == "BLOCKED":
        return make_error(
            409,
            "VEHICLE_BLOCKED",
            f"Vehicle {vehicle_id} is blocked"
        )

    if vehicle["status"] != "AVAILABLE":
        return make_error(
            409,
            "VEHICLE_NOT_AVAILABLE",
            f"Vehicle {vehicle_id} is not available for booking"
        )

    parsed_start_date = parse_iso_datetime(start_date)

    if parsed_start_date is None:
        return make_error(
            400,
            "INVALID_START_DATE_FORMAT",
            "startDate must be ISO-8601 date-time"
        )

    now = datetime.now(timezone.utc)

    if parsed_start_date <= now:
        return make_error(
            400,
            "START_DATE_MUST_BE_IN_FUTURE",
            "Booking startDate must be in the future"
        )

    booking_id = f"booking-{booking_counter}"
    booking_counter += 1

    booking = {
        "bookingId": booking_id,
        "userId": user_id,
        "vehicleId": vehicle_id,
        "startDate": start_date,
        "status": "CREATED"
    }

    bookings[booking_id] = booking
    vehicles[vehicle_id]["status"] = "BOOKED"

    if mode == "missing_booking_id":
        response = dict(booking)
        response.pop("bookingId")
        return jsonify(response), 201

    if mode == "wrong_status":
        response = dict(booking)
        response["status"] = "NEW"
        return jsonify(response), 201

    return jsonify(booking), 201


@app.route("/v1/bookings/<booking_id>", methods=["GET"])
def get_booking_by_id(booking_id):
    """
    Основной шаг сценария:
    Получение информации о бронировании.

    Управляемые режимы для проверки агента:
    - ?mode=malformed    -> некорректная структура ответа
    - ?mode=wrong_status -> неверный статус бронирования
    """

    mode = request.args.get("mode")

    if booking_id not in bookings:
        return make_error(
            404,
            "BOOKING_NOT_FOUND",
            f"Booking with id {booking_id} was not found"
        )

    booking = bookings[booking_id]

    if mode == "malformed":
        return jsonify({
            "id": booking["bookingId"],
            "state": booking["status"]
        }), 200

    if mode == "wrong_status":
        response = dict(booking)
        response["status"] = "UNKNOWN"
        return jsonify(response), 200

    return jsonify(booking), 200


@app.route("/v1/bookings/<booking_id>/cancel", methods=["POST"])
def cancel_booking(booking_id):
    """
    Основной шаг сценария:
    Отмена бронирования.

    Управляемые режимы для проверки агента:
    - ?mode=wrong_status -> HTTP 200, но статус не CANCELLED
    - ?mode=server_error -> ошибка сервера
    """

    mode = request.args.get("mode")

    if mode == "server_error":
        return make_error(
            500,
            "CANCELLATION_SERVICE_ERROR",
            "Cancellation service is temporarily unavailable"
        )

    if booking_id not in bookings:
        return make_error(
            404,
            "BOOKING_NOT_FOUND",
            f"Booking with id {booking_id} was not found"
        )

    booking = bookings[booking_id]

    if booking["status"] == "CANCELLED":
        return make_error(
            409,
            "BOOKING_ALREADY_CANCELLED",
            f"Booking {booking_id} is already cancelled"
        )

    booking["status"] = "CANCELLED"

    vehicle_id = booking["vehicleId"]

    if vehicle_id in vehicles:
        vehicles[vehicle_id]["status"] = "AVAILABLE"

    if mode == "wrong_status":
        response = dict(booking)
        response["status"] = "CREATED"
        return jsonify(response), 200

    return jsonify(booking), 200


@app.route("/v1/debug/state", methods=["GET"])
def debug_state():
    return jsonify({
        "users": users,
        "vehicles": vehicles,
        "bookings": bookings
    }), 200


@app.route("/v1/debug/reset", methods=["POST"])
def debug_reset():
    global booking_counter, users, vehicles
    global accidents, accident_counter, participant_counter, acc_vehicle_counter
    global claim_counter, assessment_counter, dtp_payment_counter

    users = copy.deepcopy(_INITIAL_USERS)
    vehicles = copy.deepcopy(_INITIAL_VEHICLES)
    bookings.clear()
    booking_counter = 1

    accidents.clear()
    accident_counter = 1
    participant_counter = 1
    acc_vehicle_counter = 1
    claim_counter = 1
    assessment_counter = 1
    dtp_payment_counter = 1

    return jsonify({
        "status": "RESET_DONE"
    }), 200


@app.route("/v1/debug/users", methods=["GET"])
def debug_get_users():
    return jsonify(users), 200


@app.route("/v1/debug/users/<user_id>/block", methods=["POST"])
def debug_block_user(user_id):
    if user_id not in users:
        users[user_id] = {"userId": user_id, "status": "BLOCKED"}
    users[user_id]["status"] = "BLOCKED"
    return jsonify({"userId": user_id, "status": "BLOCKED"}), 200


@app.route("/v1/debug/users/<user_id>/unblock", methods=["POST"])
def debug_unblock_user(user_id):
    if user_id not in users:
        users[user_id] = {"userId": user_id, "status": "ACTIVE"}
    users[user_id]["status"] = "ACTIVE"
    return jsonify({"userId": user_id, "status": "ACTIVE"}), 200


@app.route("/v1/debug/vehicles/<vehicle_id>/set-status", methods=["POST"])
def debug_set_vehicle_status(vehicle_id):
    body = request.get_json(silent=True) or {}
    new_status = body.get("status")
    if vehicle_id not in vehicles:
        vehicles[vehicle_id] = {"vehicleId": vehicle_id, "status": new_status}
    else:
        vehicles[vehicle_id]["status"] = new_status
    return jsonify(vehicles[vehicle_id]), 200


# ─────────────────────────────────────────────────────────────────────────────
# DTP (ДТП) routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/v1/accidents", methods=["POST"])
def create_accident():
    """
    Основной шаг сценария:
    Регистрация нового ДТП.

    Обязательные поля тела запроса: date, location.
    Возвращает 400 при отсутствии обязательных полей.
    Возвращает 201 с объектом Accident в статусе REGISTERED.
    """
    global accident_counter

    body = request.get_json(silent=True)

    if body is None:
        return make_error(400, "EMPTY_BODY", "Request body is required")

    date = body.get("date")
    location = body.get("location")
    description = body.get("description")

    if not date:
        return make_error(400, "DATE_REQUIRED", "Field date is required")

    if not location:
        return make_error(400, "LOCATION_REQUIRED", "Field location is required")

    accident_id = f"accident-{accident_counter}"
    accident_counter += 1

    accident = {
        "accidentId": accident_id,
        "date": date,
        "location": location,
        "description": description,
        "status": "REGISTERED",
        "participantsCount": 0,
        "totalDamageAmount": 0.0,
        "_participants": {},
        "_claims": {},
    }

    accidents[accident_id] = accident

    return jsonify(_accident_public(accident)), 201


@app.route("/v1/accidents", methods=["GET"])
def list_accidents():
    """
    Основной шаг сценария:
    Получение списка всех зарегистрированных ДТП.

    Возвращает 200 со списком объектов Accident.
    """
    return jsonify([_accident_public(a) for a in accidents.values()]), 200


@app.route("/v1/accidents/<accident_id>", methods=["GET"])
def get_accident(accident_id):
    """
    Основной шаг сценария:
    Получение данных ДТП по идентификатору.

    Возвращает 404, если ДТП не найдено.
    Возвращает 200 с полным объектом Accident, включая participantsCount.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    return jsonify(_accident_public(accidents[accident_id])), 200


@app.route("/v1/accidents/<accident_id>/participants", methods=["POST"])
def add_participant(accident_id):
    """
    Основной шаг сценария:
    Добавление участника к ДТП.

    Обязательные поля тела запроса: role, name, phone.
    role должен быть одним из: CULPRIT, VICTIM, WITNESS.
    Возвращает 404, если ДТП не найдено.
    Возвращает 400 при ошибке валидации.
    Возвращает 201 с объектом AccidentParticipant.
    """
    global participant_counter

    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    body = request.get_json(silent=True)

    if body is None:
        return make_error(400, "EMPTY_BODY", "Request body is required")

    role = body.get("role")
    name = body.get("name")
    phone = body.get("phone")
    insurance_policy = body.get("insurancePolicy")
    fault_percentage = body.get("faultPercentage", 0)

    if not role:
        return make_error(400, "ROLE_REQUIRED", "Field role is required")

    if role not in ("CULPRIT", "VICTIM", "WITNESS"):
        return make_error(400, "INVALID_ROLE", "role must be CULPRIT, VICTIM or WITNESS")

    if not name:
        return make_error(400, "NAME_REQUIRED", "Field name is required")

    if not phone:
        return make_error(400, "PHONE_REQUIRED", "Field phone is required")

    participant_id = f"participant-{participant_counter}"
    participant_counter += 1

    participant = {
        "participantId": participant_id,
        "accidentId": accident_id,
        "role": role,
        "name": name,
        "phone": phone,
        "insurancePolicy": insurance_policy,
        "faultPercentage": fault_percentage,
        "status": "ACTIVE",
        "_vehicles": {},
    }

    accident = accidents[accident_id]
    accident["_participants"][participant_id] = participant
    accident["participantsCount"] = len(accident["_participants"])

    return jsonify(_participant_public(participant)), 201


@app.route("/v1/accidents/<accident_id>/participants", methods=["GET"])
def list_participants(accident_id):
    """
    Основной шаг сценария:
    Получение списка участников ДТП.

    Возвращает 404, если ДТП не найдено.
    Возвращает 200 со списком объектов AccidentParticipant.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    participants = accidents[accident_id]["_participants"]
    return jsonify([_participant_public(p) for p in participants.values()]), 200


@app.route("/v1/accidents/<accident_id>/participants/<participant_id>", methods=["GET"])
def get_participant(accident_id, participant_id):
    """
    Основной шаг сценария:
    Получение данных участника ДТП по идентификатору.

    Возвращает 404, если ДТП или участник не найдены.
    Возвращает 200 с объектом AccidentParticipant.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    participants = accidents[accident_id]["_participants"]

    if participant_id not in participants:
        return make_error(404, "PARTICIPANT_NOT_FOUND", f"Participant '{participant_id}' not found")

    return jsonify(_participant_public(participants[participant_id])), 200


@app.route("/v1/accidents/<accident_id>/participants/<participant_id>", methods=["PATCH"])
def update_participant(accident_id, participant_id):
    """
    Основной шаг сценария:
    Обновление данных участника ДТП.

    Можно обновить: faultPercentage, status.
    Возвращает 404, если ДТП или участник не найдены.
    Возвращает 200 с обновлённым объектом AccidentParticipant.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    participants = accidents[accident_id]["_participants"]

    if participant_id not in participants:
        return make_error(404, "PARTICIPANT_NOT_FOUND", f"Participant '{participant_id}' not found")

    body = request.get_json(silent=True) or {}
    participant = participants[participant_id]

    if "faultPercentage" in body:
        participant["faultPercentage"] = body["faultPercentage"]

    if "status" in body:
        if body["status"] not in ("ACTIVE", "WITHDRAWN"):
            return make_error(400, "INVALID_STATUS", "status must be ACTIVE or WITHDRAWN")
        participant["status"] = body["status"]

    return jsonify(_participant_public(participant)), 200


@app.route("/v1/accidents/<accident_id>/participants/<participant_id>/vehicles", methods=["POST"])
def add_vehicle(accident_id, participant_id):
    """
    Основной шаг сценария:
    Добавление транспортного средства участника ДТП.

    Обязательные поля тела запроса: licensePlate, brand, model.
    Возвращает 404, если ДТП или участник не найдены.
    Возвращает 400 при ошибке валидации.
    Возвращает 201 с объектом AccidentVehicle.
    """
    global acc_vehicle_counter

    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    participants = accidents[accident_id]["_participants"]

    if participant_id not in participants:
        return make_error(404, "PARTICIPANT_NOT_FOUND", f"Participant '{participant_id}' not found")

    body = request.get_json(silent=True)

    if body is None:
        return make_error(400, "EMPTY_BODY", "Request body is required")

    license_plate = body.get("licensePlate")
    brand = body.get("brand")
    model = body.get("model")
    damage_description = body.get("damageDescription")
    damage_amount = body.get("damageAmount", 0.0)

    if not license_plate:
        return make_error(400, "LICENSE_PLATE_REQUIRED", "Field licensePlate is required")

    if not brand:
        return make_error(400, "BRAND_REQUIRED", "Field brand is required")

    if not model:
        return make_error(400, "MODEL_REQUIRED", "Field model is required")

    vehicle_id = f"acc-vehicle-{acc_vehicle_counter}"
    acc_vehicle_counter += 1

    acc_vehicle = {
        "accidentVehicleId": vehicle_id,
        "participantId": participant_id,
        "licensePlate": license_plate,
        "brand": brand,
        "model": model,
        "damageDescription": damage_description,
        "damageAmount": damage_amount,
    }

    participants[participant_id]["_vehicles"][vehicle_id] = acc_vehicle

    # Update totalDamageAmount on the accident
    _recalculate_damage(accidents[accident_id])

    return jsonify(acc_vehicle), 201


@app.route("/v1/accidents/<accident_id>/participants/<participant_id>/vehicles", methods=["GET"])
def list_vehicles(accident_id, participant_id):
    """
    Основной шаг сценария:
    Получение списка транспортных средств участника ДТП.

    Возвращает 404, если ДТП или участник не найдены.
    Возвращает 200 со списком объектов AccidentVehicle.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    participants = accidents[accident_id]["_participants"]

    if participant_id not in participants:
        return make_error(404, "PARTICIPANT_NOT_FOUND", f"Participant '{participant_id}' not found")

    vehicle_list = list(participants[participant_id]["_vehicles"].values())
    return jsonify(vehicle_list), 200


@app.route("/v1/accidents/<accident_id>/claims", methods=["POST"])
def create_claim(accident_id):
    """
    Основной шаг сценария:
    Создание страхового требования по ДТП.

    Обязательные поля тела запроса: claimantParticipantId.
    Участник должен быть в роли VICTIM или WITNESS.
    Возвращает 404, если ДТП или участник не найдены.
    Возвращает 409, если требование для данного участника уже существует.
    Возвращает 201 с объектом InsuranceClaim в статусе DRAFT.
    """
    global claim_counter

    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    body = request.get_json(silent=True)

    if body is None:
        return make_error(400, "EMPTY_BODY", "Request body is required")

    claimant_participant_id = body.get("claimantParticipantId")
    claimed_amount = body.get("claimedAmount", 0.0)

    if not claimant_participant_id:
        return make_error(400, "CLAIMANT_PARTICIPANT_ID_REQUIRED", "Field claimantParticipantId is required")

    participants = accidents[accident_id]["_participants"]

    if claimant_participant_id not in participants:
        return make_error(404, "PARTICIPANT_NOT_FOUND", f"Participant '{claimant_participant_id}' not found")

    participant = participants[claimant_participant_id]

    if participant["role"] not in ("VICTIM", "WITNESS"):
        return make_error(400, "INVALID_PARTICIPANT_ROLE",
                          "Only participants with role VICTIM or WITNESS can file a claim")

    # Check for duplicate claim for the same participant
    claims = accidents[accident_id]["_claims"]
    for existing_claim in claims.values():
        if existing_claim["claimantParticipantId"] == claimant_participant_id:
            return make_error(409, "CLAIM_ALREADY_EXISTS",
                              f"A claim for participant '{claimant_participant_id}' already exists")

    claim_id = f"claim-{claim_counter}"
    claim_counter += 1

    claim = {
        "claimId": claim_id,
        "accidentId": accident_id,
        "claimantParticipantId": claimant_participant_id,
        "status": "DRAFT",
        "claimedAmount": claimed_amount,
        "approvedAmount": 0.0,
        "rejectionReason": None,
        "_assessments": {},
        "_payments": {},
    }

    claims[claim_id] = claim

    return jsonify(_claim_public(claim)), 201


@app.route("/v1/accidents/<accident_id>/claims", methods=["GET"])
def list_claims(accident_id):
    """
    Основной шаг сценария:
    Получение списка страховых требований по ДТП.

    Возвращает 404, если ДТП не найдено.
    Возвращает 200 со списком объектов InsuranceClaim.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]
    return jsonify([_claim_public(c) for c in claims.values()]), 200


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>", methods=["GET"])
def get_claim(accident_id, claim_id):
    """
    Основной шаг сценария:
    Получение страхового требования по идентификатору.

    Возвращает 404, если ДТП или требование не найдены.
    Возвращает 200 с объектом InsuranceClaim.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    return jsonify(_claim_public(claims[claim_id])), 200


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>", methods=["PATCH"])
def update_claim(accident_id, claim_id):
    """
    Основной шаг сценария:
    Обновление страхового требования (только в статусе DRAFT).

    Можно обновить: claimedAmount, description.
    Возвращает 409, если требование не в статусе DRAFT.
    Возвращает 404, если ДТП или требование не найдены.
    Возвращает 200 с обновлённым объектом InsuranceClaim.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    claim = claims[claim_id]

    if claim["status"] != "DRAFT":
        return make_error(409, "CLAIM_NOT_EDITABLE",
                          f"Claim '{claim_id}' can only be updated in DRAFT status, current: {claim['status']}")

    body = request.get_json(silent=True) or {}

    if "claimedAmount" in body:
        claim["claimedAmount"] = body["claimedAmount"]

    if "description" in body:
        claim["description"] = body["description"]

    return jsonify(_claim_public(claim)), 200


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>/submit", methods=["POST"])
def submit_claim(accident_id, claim_id):
    """
    Основной шаг сценария:
    Подача страхового требования на рассмотрение (DRAFT → SUBMITTED).

    Требование должно иметь claimedAmount > 0, иначе возвращает 400 CLAIM_AMOUNT_REQUIRED.
    Возвращает 404, если ДТП или требование не найдены.
    Возвращает 200 с обновлённым объектом InsuranceClaim.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    claim = claims[claim_id]

    if not claim.get("claimedAmount") or claim["claimedAmount"] <= 0:
        return make_error(400, "CLAIM_AMOUNT_REQUIRED",
                          "claimedAmount must be greater than 0 to submit the claim")

    claim["status"] = "SUBMITTED"

    return jsonify(_claim_public(claim)), 200


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>/assessments", methods=["POST"])
def create_assessment(accident_id, claim_id):
    """
    Основной шаг сценария:
    Создание экспертной оценки по страховому требованию.

    Обязательные поля тела запроса: expertName, scheduledDate.
    Возвращает 404, если ДТП или требование не найдены.
    Возвращает 400 при ошибке валидации.
    Возвращает 201 с объектом ExpertAssessment в статусе SCHEDULED.
    """
    global assessment_counter

    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    body = request.get_json(silent=True)

    if body is None:
        return make_error(400, "EMPTY_BODY", "Request body is required")

    expert_name = body.get("expertName")
    scheduled_date = body.get("scheduledDate")

    if not expert_name:
        return make_error(400, "EXPERT_NAME_REQUIRED", "Field expertName is required")

    if not scheduled_date:
        return make_error(400, "SCHEDULED_DATE_REQUIRED", "Field scheduledDate is required")

    assessment_id = f"assessment-{assessment_counter}"
    assessment_counter += 1

    assessment = {
        "assessmentId": assessment_id,
        "claimId": claim_id,
        "expertName": expert_name,
        "scheduledDate": scheduled_date,
        "completedDate": None,
        "assessedAmount": 0.0,
        "report": None,
        "status": "SCHEDULED",
    }

    claims[claim_id]["_assessments"][assessment_id] = assessment

    return jsonify(assessment), 201


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>/assessments/<assessment_id>", methods=["GET"])
def get_assessment(accident_id, claim_id, assessment_id):
    """
    Основной шаг сценария:
    Получение экспертной оценки по идентификатору.

    Возвращает 404, если ДТП, требование или оценка не найдены.
    Возвращает 200 с объектом ExpertAssessment.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    assessments = claims[claim_id]["_assessments"]

    if assessment_id not in assessments:
        return make_error(404, "ASSESSMENT_NOT_FOUND", f"Assessment '{assessment_id}' not found")

    return jsonify(assessments[assessment_id]), 200


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>/assessments/<assessment_id>", methods=["PATCH"])
def update_assessment(accident_id, claim_id, assessment_id):
    """
    Основной шаг сценария:
    Обновление экспертной оценки.

    Можно обновить: assessedAmount, report, status.
    Когда status установлен в COMPLETED:
      - Статус требования меняется на UNDER_REVIEW
      - approvedAmount требования = assessedAmount оценки
      - completedDate оценки = текущее время UTC
    Возвращает 404, если ДТП, требование или оценка не найдены.
    Возвращает 200 с обновлённым объектом ExpertAssessment.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    assessments = claims[claim_id]["_assessments"]

    if assessment_id not in assessments:
        return make_error(404, "ASSESSMENT_NOT_FOUND", f"Assessment '{assessment_id}' not found")

    body = request.get_json(silent=True) or {}
    assessment = assessments[assessment_id]
    claim = claims[claim_id]

    if "assessedAmount" in body:
        assessment["assessedAmount"] = body["assessedAmount"]

    if "report" in body:
        assessment["report"] = body["report"]

    if "status" in body:
        if body["status"] not in ("SCHEDULED", "IN_PROGRESS", "COMPLETED"):
            return make_error(400, "INVALID_STATUS", "status must be SCHEDULED, IN_PROGRESS or COMPLETED")
        assessment["status"] = body["status"]

        if body["status"] == "COMPLETED":
            assessment["completedDate"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            claim["status"] = "UNDER_REVIEW"
            claim["approvedAmount"] = assessment["assessedAmount"]

    return jsonify(assessment), 200


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>/payments", methods=["POST"])
def create_dtp_payment(accident_id, claim_id):
    """
    Основной шаг сценария:
    Создание страховой выплаты по требованию.

    Требование должно быть в статусе UNDER_REVIEW или APPROVED.
    Не допускается дублирование: возвращает 409 PAYMENT_ALREADY_EXISTS,
    если уже есть выплата в статусе PENDING или CONFIRMED.
    Обязательные поля тела запроса: amount.
    Возвращает 404, если ДТП или требование не найдены.
    Возвращает 201 с объектом DTPPayment в статусе PENDING.
    """
    global dtp_payment_counter

    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    claim = claims[claim_id]

    if claim["status"] not in ("UNDER_REVIEW", "APPROVED"):
        return make_error(409, "CLAIM_NOT_PAYABLE",
                          f"Claim '{claim_id}' must be UNDER_REVIEW or APPROVED to create a payment, "
                          f"current: {claim['status']}")

    # Check for duplicate active payment
    payments = claim["_payments"]
    for existing_payment in payments.values():
        if existing_payment["status"] in ("PENDING", "CONFIRMED"):
            return make_error(409, "PAYMENT_ALREADY_EXISTS",
                              f"An active payment already exists for claim '{claim_id}'")

    body = request.get_json(silent=True)

    if body is None:
        return make_error(400, "EMPTY_BODY", "Request body is required")

    amount = body.get("amount")

    if amount is None:
        return make_error(400, "AMOUNT_REQUIRED", "Field amount is required")

    payment_id = f"dtp-payment-{dtp_payment_counter}"
    dtp_payment_counter += 1

    payment = {
        "paymentId": payment_id,
        "claimId": claim_id,
        "claimantParticipantId": claim["claimantParticipantId"],
        "amount": amount,
        "status": "PENDING",
        "confirmedAt": None,
    }

    payments[payment_id] = payment

    return jsonify(payment), 201


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>/payments", methods=["GET"])
def list_dtp_payments(accident_id, claim_id):
    """
    Основной шаг сценария:
    Получение списка выплат по страховому требованию.

    Возвращает 404, если ДТП или требование не найдены.
    Возвращает 200 со списком объектов DTPPayment.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    payment_list = list(claims[claim_id]["_payments"].values())
    return jsonify(payment_list), 200


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>/payments/<payment_id>", methods=["GET"])
def get_dtp_payment(accident_id, claim_id, payment_id):
    """
    Основной шаг сценария:
    Получение страховой выплаты по идентификатору.

    Возвращает 404, если ДТП, требование или выплата не найдены.
    Возвращает 200 с объектом DTPPayment.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    payments = claims[claim_id]["_payments"]

    if payment_id not in payments:
        return make_error(404, "PAYMENT_NOT_FOUND", f"Payment '{payment_id}' not found")

    return jsonify(payments[payment_id]), 200


@app.route("/v1/accidents/<accident_id>/claims/<claim_id>/payments/<payment_id>/confirm", methods=["POST"])
def confirm_dtp_payment(accident_id, claim_id, payment_id):
    """
    Основной шаг сценария:
    Подтверждение страховой выплаты (PENDING → CONFIRMED).

    Выплата должна быть в статусе PENDING.
    После подтверждения: status=CONFIRMED, confirmedAt=текущее время UTC,
    статус требования меняется на PAID.
    Возвращает 404, если ДТП, требование или выплата не найдены.
    Возвращает 409, если выплата не в статусе PENDING.
    Возвращает 200 с обновлённым объектом DTPPayment.
    """
    if accident_id not in accidents:
        return make_error(404, "ACCIDENT_NOT_FOUND", f"Accident '{accident_id}' not found")

    claims = accidents[accident_id]["_claims"]

    if claim_id not in claims:
        return make_error(404, "CLAIM_NOT_FOUND", f"Claim '{claim_id}' not found")

    payments = claims[claim_id]["_payments"]

    if payment_id not in payments:
        return make_error(404, "PAYMENT_NOT_FOUND", f"Payment '{payment_id}' not found")

    payment = payments[payment_id]

    if payment["status"] != "PENDING":
        return make_error(409, "PAYMENT_NOT_PENDING",
                          f"Payment '{payment_id}' must be PENDING to confirm, current: {payment['status']}")

    payment["status"] = "CONFIRMED"
    payment["confirmedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    claims[claim_id]["status"] = "PAID"

    return jsonify(payment), 200


# ─── DTP helper functions ────────────────────────────────────────────────────

def _accident_public(accident: dict) -> dict:
    """Return Accident object without internal keys."""
    return {k: v for k, v in accident.items() if not k.startswith("_")}


def _participant_public(participant: dict) -> dict:
    """Return AccidentParticipant object without internal keys."""
    return {k: v for k, v in participant.items() if not k.startswith("_")}


def _claim_public(claim: dict) -> dict:
    """Return InsuranceClaim object without internal keys."""
    return {k: v for k, v in claim.items() if not k.startswith("_")}


def _recalculate_damage(accident: dict) -> None:
    """Sum damageAmount across all vehicles of all participants."""
    total = 0.0
    for participant in accident["_participants"].values():
        for v in participant["_vehicles"].values():
            total += v.get("damageAmount") or 0.0
    accident["totalDamageAmount"] = total


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080,
        debug=True
    )
