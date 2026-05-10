import uuid
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request
from ..state import store
from ..models import BookingStatus, VehicleStatus
from ..validators import (
    flk_user_full, flk_user_no_critical_debt, flk_user_no_active_rental,
    flk_license_valid, flk_payment_method_exists, flk_vehicle_available,
    flk_booking_active, flk_booking_confirmed, flk_booking_not_expired,
)
from ..logger_config import get_logger

bp = Blueprint("bookings", __name__, url_prefix="/v1")
log = get_logger()

BOOKING_TTL_MINUTES = 15


def _err(code, msg, status):
    log.warning("Booking validation failed: %s — %s", code, msg)
    return jsonify({"error": code, "message": msg}), status


@bp.route("/bookings", methods=["POST"])
def create_booking():
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId", "")
    vehicle_id = body.get("vehicleId", "")
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))

    log.info("POST /bookings userId=%s vehicleId=%s corr=%s", user_id, vehicle_id, correlation_id)

    for check, args in [
        (flk_user_full, (user_id,)),
        (flk_user_no_critical_debt, (user_id,)),
        (flk_user_no_active_rental, (user_id,)),
        (flk_license_valid, (user_id,)),
        (flk_payment_method_exists, (user_id,)),
        (flk_vehicle_available, (vehicle_id,)),
    ]:
        ok, code, http_status, msg = check(*args)
        if not ok:
            return _err(code, msg, http_status)

    booking_id = f"booking-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=BOOKING_TTL_MINUTES)

    booking = {
        "bookingId": booking_id,
        "userId": user_id,
        "vehicleId": vehicle_id,
        "status": BookingStatus.CONFIRMED,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    store.db["bookings"][booking_id] = booking
    store.db["vehicles"][vehicle_id]["status"] = VehicleStatus.RESERVED

    store.add_outbox("BOOKING_CREATED", {"bookingId": booking_id, "userId": user_id, "vehicleId": vehicle_id})
    store.add_audit("booking", booking_id, "N/A", BookingStatus.CONFIRMED, "booking created", "api", correlation_id)

    log.info("Booking created: %s vehicle=%s user=%s expires=%s", booking_id, vehicle_id, user_id, expires_at.isoformat())
    return jsonify(booking), 201


@bp.route("/bookings/<booking_id>", methods=["GET"])
def get_booking(booking_id: str):
    log.info("GET /bookings/%s", booking_id)
    b = store.db["bookings"].get(booking_id)
    if not b:
        return jsonify({"error": "BOOKING_NOT_FOUND", "message": f"Booking '{booking_id}' not found"}), 404
    return jsonify(b), 200


@bp.route("/bookings/<booking_id>/cancel", methods=["POST"])
def cancel_booking(booking_id: str):
    body = request.get_json(silent=True) or {}
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    log.info("POST /bookings/%s/cancel corr=%s", booking_id, correlation_id)

    ok, code, http_status, msg = flk_booking_active(booking_id)
    if not ok:
        return _err(code, msg, http_status)

    b = store.db["bookings"][booking_id]
    prev_status = b["status"]
    b["status"] = BookingStatus.CANCELLED
    b["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    b["cancel_reason"] = body.get("reason", "USER_REQUEST")

    vehicle_id = b["vehicleId"]
    if store.db["vehicles"].get(vehicle_id, {}).get("status") == VehicleStatus.RESERVED:
        store.db["vehicles"][vehicle_id]["status"] = VehicleStatus.AVAILABLE
        log.info("Vehicle %s released back to AVAILABLE after booking cancel", vehicle_id)

    store.add_outbox("BOOKING_CANCELLED", {"bookingId": booking_id, "vehicleId": vehicle_id})
    store.add_audit("booking", booking_id, prev_status, BookingStatus.CANCELLED, "user cancelled", "api", correlation_id)

    log.info("Booking cancelled: %s", booking_id)
    return jsonify(store.db["bookings"][booking_id]), 200


@bp.route("/bookings/<booking_id>/expire", methods=["POST"])
def expire_booking(booking_id: str):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    log.info("POST /bookings/%s/expire corr=%s", booking_id, correlation_id)

    b = store.db["bookings"].get(booking_id)
    if not b:
        return jsonify({"error": "BOOKING_NOT_FOUND", "message": f"Booking '{booking_id}' not found"}), 404

    if b["status"] not in (BookingStatus.CONFIRMED, BookingStatus.CREATED):
        return _err(
            "BOOKING_STATUS_INVALID",
            f"Cannot expire booking in status '{b['status']}'",
            409,
        )

    prev_status = b["status"]
    b["status"] = BookingStatus.EXPIRED
    b["expired_at"] = datetime.now(timezone.utc).isoformat()

    vehicle_id = b["vehicleId"]
    if store.db["vehicles"].get(vehicle_id, {}).get("status") == VehicleStatus.RESERVED:
        store.db["vehicles"][vehicle_id]["status"] = VehicleStatus.AVAILABLE
        log.info("Vehicle %s released back to AVAILABLE after booking expire", vehicle_id)

    store.add_outbox("BOOKING_EXPIRED", {"bookingId": booking_id, "vehicleId": vehicle_id})
    store.add_audit("booking", booking_id, prev_status, BookingStatus.EXPIRED, "ttl expired", "scheduler", correlation_id)

    log.info("Booking expired: %s", booking_id)
    return jsonify(store.db["bookings"][booking_id]), 200
