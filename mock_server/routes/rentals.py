import uuid
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from ..state import store
from ..models import BookingStatus, VehicleStatus, RentalStatus
from ..validators import (
    flk_user_full, flk_user_no_active_rental,
    flk_booking_confirmed, flk_booking_not_expired,
    flk_vehicle_reserved, flk_vehicle_in_zone,
    flk_rental_exists, flk_rental_started,
    flk_geo_service_available,
)
from ..logger_config import get_logger

bp = Blueprint("rentals", __name__, url_prefix="/v1")
log = get_logger()


def _err(code, msg, status):
    log.warning("Rental validation failed: %s — %s", code, msg)
    return jsonify({"error": code, "message": msg}), status


@bp.route("/rentals/start", methods=["POST"])
def start_rental():
    body = request.get_json(silent=True) or {}
    booking_id = body.get("bookingId", "")
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))

    log.info("POST /rentals/start bookingId=%s corr=%s", booking_id, correlation_id)

    ok, code, http_status, msg = flk_booking_confirmed(booking_id)
    if not ok:
        return _err(code, msg, http_status)

    ok, code, http_status, msg = flk_booking_not_expired(booking_id)
    if not ok:
        return _err(code, msg, http_status)

    booking = store.db["bookings"][booking_id]
    user_id = booking["userId"]
    vehicle_id = booking["vehicleId"]

    for check, args in [
        (flk_user_full, (user_id,)),
        (flk_user_no_active_rental, (user_id,)),
        (flk_vehicle_reserved, (vehicle_id,)),
    ]:
        ok, code, http_status, msg = check(*args)
        if not ok:
            return _err(code, msg, http_status)

    # Geo-service check — distance between user and vehicle
    user_location = body.get("userLocation")
    ok, code, http_status, msg = flk_geo_service_available()
    if not ok:
        return _err(code, msg, http_status)
    # If location provided and geo service is ok, we accept any distance in mock
    log.debug("Geo check passed for user=%s vehicle=%s", user_id, vehicle_id)

    # === Transaction: create rental, update booking + vehicle ===
    rental_id = f"rental-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    rental = {
        "rentalId": rental_id,
        "bookingId": booking_id,
        "userId": user_id,
        "vehicleId": vehicle_id,
        "status": RentalStatus.STARTED,
        "started_at": now,
        "finished_at": None,
        "tariff_per_minute": store.db["vehicles"][vehicle_id].get("tariff_per_minute", 8.0),
    }
    store.db["rental_sessions"][rental_id] = rental
    store.db["bookings"][booking_id]["status"] = BookingStatus.STARTED
    store.db["vehicles"][vehicle_id]["status"] = VehicleStatus.IN_USE

    store.add_outbox("RENTAL_STARTED", {"rentalId": rental_id, "bookingId": booking_id, "userId": user_id, "vehicleId": vehicle_id})
    store.add_audit("rental", rental_id, "N/A", RentalStatus.STARTED, "rental started", "api", correlation_id)
    store.add_audit("booking", booking_id, BookingStatus.CONFIRMED, BookingStatus.STARTED, "rental started", "api", correlation_id)
    store.add_audit("vehicle", vehicle_id, VehicleStatus.RESERVED, VehicleStatus.IN_USE, "rental started", "api", correlation_id)

    log.info("Rental started: %s user=%s vehicle=%s", rental_id, user_id, vehicle_id)
    return jsonify(rental), 201


@bp.route("/rentals/<rental_id>", methods=["GET"])
def get_rental(rental_id: str):
    log.info("GET /rentals/%s", rental_id)
    ok, code, http_status, msg = flk_rental_exists(rental_id)
    if not ok:
        return jsonify({"error": code, "message": msg}), http_status
    r = store.db["rental_sessions"][rental_id]

    result = dict(r)
    # Attach preliminary amount if rental is STARTED
    if r["status"] == RentalStatus.STARTED and r.get("started_at"):
        started = datetime.fromisoformat(r["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        minutes = (datetime.now(timezone.utc) - started).total_seconds() / 60
        result["preliminary_amount"] = round(minutes * r.get("tariff_per_minute", 8.0), 2)

    return jsonify(result), 200


@bp.route("/rentals/<rental_id>/finish", methods=["POST"])
def finish_rental(rental_id: str):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    log.info("POST /rentals/%s/finish corr=%s", rental_id, correlation_id)

    ok, code, http_status, msg = flk_rental_started(rental_id)
    if not ok:
        # Case 012: repeated finish — rental already FINISHED → idempotent 409
        return _err(code, msg, http_status)

    rental = store.db["rental_sessions"][rental_id]
    vehicle_id = rental["vehicleId"]

    # Zone check (case 006)
    ok, code, http_status, msg = flk_vehicle_in_zone(vehicle_id)
    if not ok:
        return _err(code, msg, http_status)

    # Technically blocked finish (configurable)
    if rental_id in store.mock_config.get("rentals_blocked_finish", []):
        return _err("RENTAL_FINISH_BLOCKED", "Rental cannot be finished due to a critical technical event", 409)

    # Telemetry check: engine off, doors closed, speed = 0
    telem = store.db["vehicle_telemetry"].get(vehicle_id, {})
    if telem.get("engine_on", False):
        return _err("ENGINE_NOT_OFF", "Engine must be off before finishing rental", 409)
    if not telem.get("doors_closed", True):
        return _err("DOORS_NOT_CLOSED", "Doors must be closed before finishing rental", 409)
    if telem.get("speed_kmh", 0) != 0:
        return _err("VEHICLE_MOVING", "Vehicle must be stationary before finishing rental", 409)

    # === Transaction ===
    now = datetime.now(timezone.utc)
    started = datetime.fromisoformat(rental["started_at"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    total_minutes = max(1, round((now - started).total_seconds() / 60))
    amount = round(total_minutes * rental.get("tariff_per_minute", 8.0), 2)

    rental["status"] = RentalStatus.FINISHED
    rental["finished_at"] = now.isoformat()
    rental["total_minutes"] = total_minutes
    rental["amount"] = amount

    store.db["vehicles"][vehicle_id]["status"] = VehicleStatus.AVAILABLE

    store.add_outbox("RENTAL_FINISHED", {"rentalId": rental_id, "vehicleId": vehicle_id, "amount": amount})
    store.add_outbox("VEHICLE_RELEASED", {"vehicleId": vehicle_id, "rentalId": rental_id})
    store.add_audit("rental", rental_id, RentalStatus.STARTED, RentalStatus.FINISHED, "rental finished", "api", correlation_id)
    store.add_audit("vehicle", vehicle_id, VehicleStatus.IN_USE, VehicleStatus.AVAILABLE, "rental finished", "api", correlation_id)

    log.info("Rental finished: %s minutes=%d amount=%.2f", rental_id, total_minutes, amount)
    return jsonify(store.db["rental_sessions"][rental_id]), 200
