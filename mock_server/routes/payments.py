import uuid
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from ..state import store
from ..models import PaymentStatus, RentalStatus
from ..validators import (
    flk_payment_exists, flk_payment_failed, flk_payment_capturable,
    flk_no_duplicate_payment, flk_rental_exists, flk_retry_limit_not_exceeded,
)
from ..logger_config import get_logger

bp = Blueprint("payments", __name__, url_prefix="/v1")
log = get_logger()

MAX_REFUND_RATIO = 1.0  # full refund allowed


def _err(code, msg, status):
    log.warning("Payment validation failed: %s — %s", code, msg)
    return jsonify({"error": code, "message": msg}), status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@bp.route("/payments", methods=["POST"])
def create_payment():
    body = request.get_json(silent=True) or {}
    rental_id = body.get("rentalId", "")
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))

    log.info("POST /payments rentalId=%s corr=%s", rental_id, correlation_id)

    ok, code, http_status, msg = flk_rental_exists(rental_id)
    if not ok:
        return _err(code, msg, http_status)

    rental = store.db["rental_sessions"][rental_id]
    # PAID means payment already succeeded — check for duplicate before rejecting
    if rental["status"] == RentalStatus.PAID:
        ok, code, http_status, msg = flk_no_duplicate_payment(rental_id)
        if not ok:
            return _err(code, msg, http_status)
    if rental["status"] not in (RentalStatus.FINISHED, RentalStatus.PAYMENT_FAILED):
        return _err(
            "RENTAL_NOT_FINISHED",
            f"Rental '{rental_id}' must be FINISHED to create payment (current: {rental['status']})",
            409,
        )

    ok, code, http_status, msg = flk_no_duplicate_payment(rental_id)
    if not ok:
        return _err(code, msg, http_status)

    amount = body.get("amount", rental.get("amount", 0))
    if not amount or amount <= 0:
        return _err("PAYMENT_AMOUNT_INVALID", "Payment amount must be > 0", 422)

    payment_id = f"payment-{uuid.uuid4().hex[:8]}"
    now = _now_iso()

    # Simulate payment capture via provider
    provider_available = store.mock_config.get("payment_provider_available", True)
    if not provider_available or payment_id in store.mock_config.get("payment_capture_fails", []):
        status = PaymentStatus.FAILED
        failure_reason = "PROVIDER_UNAVAILABLE" if not provider_available else "PROVIDER_ERROR"
    else:
        status = PaymentStatus.CAPTURED
        failure_reason = None

    payment = {
        "paymentId": payment_id,
        "rentalId": rental_id,
        "userId": rental.get("userId"),
        "amount": amount,
        "currency": body.get("currency", "RUB"),
        "status": status,
        "retry_count": 0,
        "created_at": now,
        "updated_at": now,
        "failure_reason": failure_reason,
    }
    store.db["payments"][payment_id] = payment

    if status == PaymentStatus.CAPTURED:
        rental["status"] = RentalStatus.PAID
        store.add_outbox("PAYMENT_CAPTURED", {"paymentId": payment_id, "rentalId": rental_id, "amount": amount})
        store.add_outbox("NOTIFICATION_REQUESTED", {"userId": rental.get("userId"), "rentalId": rental_id})
        log.info("Payment captured: %s rentalId=%s amount=%.2f", payment_id, rental_id, amount)
    else:
        rental["status"] = RentalStatus.PAYMENT_FAILED
        store.add_outbox("PAYMENT_FAILED", {"paymentId": payment_id, "rentalId": rental_id, "reason": failure_reason})
        log.warning("Payment failed: %s rentalId=%s reason=%s", payment_id, rental_id, failure_reason)

    store.add_audit("payment", payment_id, "N/A", status, "payment created", "api", correlation_id)
    return jsonify(payment), 201


@bp.route("/payments/<payment_id>", methods=["GET"])
def get_payment(payment_id: str):
    log.info("GET /payments/%s", payment_id)
    ok, code, http_status, msg = flk_payment_exists(payment_id)
    if not ok:
        return jsonify({"error": code, "message": msg}), http_status
    return jsonify(store.db["payments"][payment_id]), 200


@bp.route("/payments/<payment_id>/retry", methods=["POST"])
def retry_payment(payment_id: str):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    log.info("POST /payments/%s/retry corr=%s", payment_id, correlation_id)

    for check, args in [
        (flk_payment_exists, (payment_id,)),
        (flk_payment_failed, (payment_id,)),
        (flk_retry_limit_not_exceeded, (payment_id,)),
    ]:
        ok, code, http_status, msg = check(*args)
        if not ok:
            return _err(code, msg, http_status)

    p = store.db["payments"][payment_id]
    rental = store.db["rental_sessions"].get(p["rentalId"], {})
    if rental.get("status") not in (RentalStatus.FINISHED, RentalStatus.PAYMENT_FAILED):
        return _err("RENTAL_NOT_FINISHED", "Rental must be FINISHED to retry payment", 409)

    p["retry_count"] = p.get("retry_count", 0) + 1
    p["updated_at"] = _now_iso()
    p["status"] = PaymentStatus.RETRYING

    # Simulate provider response
    provider_available = store.mock_config.get("payment_provider_available", True)
    if not provider_available or payment_id in store.mock_config.get("payment_capture_fails", []):
        p["status"] = PaymentStatus.FAILED
        p["failure_reason"] = "PROVIDER_UNAVAILABLE"
        rental["status"] = RentalStatus.PAYMENT_FAILED
        store.add_outbox("PAYMENT_FAILED", {"paymentId": payment_id, "attempt": p["retry_count"]})
        log.warning("Payment retry failed: %s attempt=%d", payment_id, p["retry_count"])
    else:
        p["status"] = PaymentStatus.CAPTURED
        p["failure_reason"] = None
        rental["status"] = RentalStatus.PAID
        store.add_outbox("PAYMENT_CAPTURED", {"paymentId": payment_id, "rentalId": p["rentalId"], "attempt": p["retry_count"]})
        store.add_outbox("NOTIFICATION_REQUESTED", {"userId": rental.get("userId"), "rentalId": p["rentalId"]})
        log.info("Payment retry succeeded: %s attempt=%d", payment_id, p["retry_count"])

    store.add_audit("payment", payment_id, PaymentStatus.FAILED, p["status"], "payment retry", "api", correlation_id)
    return jsonify(p), 200


@bp.route("/payments/<payment_id>/refund", methods=["POST"])
def refund_payment(payment_id: str):
    body = request.get_json(silent=True) or {}
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    log.info("POST /payments/%s/refund corr=%s", payment_id, correlation_id)

    for check, args in [
        (flk_payment_exists, (payment_id,)),
        (flk_payment_capturable, (payment_id,)),
    ]:
        ok, code, http_status, msg = check(*args)
        if not ok:
            return _err(code, msg, http_status)

    p = store.db["payments"][payment_id]
    refund_amount = body.get("amount", p["amount"])

    if refund_amount <= 0:
        return _err("REFUND_AMOUNT_INVALID", "Refund amount must be > 0", 422)
    if refund_amount > p["amount"]:
        return _err("REFUND_AMOUNT_TOO_HIGH", f"Refund amount {refund_amount} exceeds payment amount {p['amount']}", 422)

    refund_id = f"refund-{uuid.uuid4().hex[:8]}"
    now = _now_iso()

    is_partial = refund_amount < p["amount"]
    prev_status = p["status"]
    p["status"] = PaymentStatus.PARTIALLY_REFUNDED if is_partial else PaymentStatus.REFUNDED
    p["updated_at"] = now
    p["refund_amount"] = refund_amount
    p["refunded_at"] = now

    refund_record = {
        "refundId": refund_id,
        "paymentId": payment_id,
        "rentalId": p["rentalId"],
        "amount": refund_amount,
        "reason": body.get("reason", "CUSTOMER_REQUEST"),
        "status": "COMPLETED",
        "created_at": now,
    }

    store.add_outbox("PAYMENT_REFUNDED", {"paymentId": payment_id, "refundId": refund_id, "amount": refund_amount})
    store.add_audit("payment", payment_id, prev_status, p["status"], "refund processed", "api", correlation_id)

    log.info("Payment refunded: %s refundId=%s amount=%.2f partial=%s", payment_id, refund_id, refund_amount, is_partial)
    return jsonify(refund_record), 201
