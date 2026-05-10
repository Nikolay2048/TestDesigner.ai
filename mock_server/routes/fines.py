import uuid
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from ..state import store
from ..models import FineStatus, RentalStatus
from ..validators import flk_rental_exists, flk_user_exists
from ..logger_config import get_logger

bp = Blueprint("fines", __name__, url_prefix="/v1")
log = get_logger()


def _err(code, msg, status):
    log.warning("Fine validation failed: %s — %s", code, msg)
    return jsonify({"error": code, "message": msg}), status


@bp.route("/fines", methods=["POST"])
def create_fine():
    body = request.get_json(silent=True) or {}
    rental_id = body.get("rentalId", "")
    user_id = body.get("userId", "")
    amount = body.get("amount")
    reason = body.get("reason", "")
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))

    log.info("POST /fines rentalId=%s userId=%s amount=%s corr=%s", rental_id, user_id, amount, correlation_id)

    if not rental_id:
        return _err("FINE_RENTAL_REQUIRED", "rentalId is required", 422)
    if not user_id:
        return _err("FINE_USER_REQUIRED", "userId is required", 422)
    if not amount or amount <= 0:
        return _err("FINE_AMOUNT_INVALID", "amount must be > 0", 422)
    if not reason:
        return _err("FINE_REASON_REQUIRED", "reason is required", 422)

    ok, code, http_status, msg = flk_rental_exists(rental_id)
    if not ok:
        return _err(code, msg, http_status)

    ok, code, http_status, msg = flk_user_exists(user_id)
    if not ok:
        return _err(code, msg, http_status)

    rental = store.db["rental_sessions"][rental_id]
    if rental["status"] not in (RentalStatus.FINISHED, RentalStatus.PAID, RentalStatus.PAYMENT_FAILED):
        return _err(
            "RENTAL_NOT_CLOSED",
            f"Fine can only be created for a finished rental (current: {rental['status']})",
            409,
        )

    fine_id = f"fine-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    fine = {
        "fineId": fine_id,
        "rentalId": rental_id,
        "userId": user_id,
        "vehicleId": rental.get("vehicleId"),
        "amount": amount,
        "currency": body.get("currency", "RUB"),
        "reason": reason,
        "status": FineStatus.CREATED,
        "fined_at": body.get("finedAt", now),
        "created_at": now,
    }
    store.db["fines"][fine_id] = fine
    store.add_outbox("FINE_CREATED", {"fineId": fine_id, "rentalId": rental_id, "userId": user_id, "amount": amount})
    store.add_audit("fine", fine_id, "N/A", FineStatus.CREATED, reason, "api", correlation_id)

    log.info("Fine created: %s rentalId=%s amount=%.2f reason=%s", fine_id, rental_id, amount, reason)
    return jsonify(fine), 201


@bp.route("/fines/<fine_id>", methods=["GET"])
def get_fine(fine_id: str):
    log.info("GET /fines/%s", fine_id)
    fine = store.db["fines"].get(fine_id)
    if not fine:
        return jsonify({"error": "FINE_NOT_FOUND", "message": f"Fine '{fine_id}' not found"}), 404
    return jsonify(fine), 200
