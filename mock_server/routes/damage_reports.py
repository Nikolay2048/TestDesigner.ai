import uuid
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from ..state import store
from ..models import DamageReportStatus, RentalStatus
from ..validators import flk_rental_exists, flk_user_exists
from ..logger_config import get_logger

bp = Blueprint("damage_reports", __name__, url_prefix="/v1")
log = get_logger()


def _err(code, msg, status):
    log.warning("DamageReport validation failed: %s — %s", code, msg)
    return jsonify({"error": code, "message": msg}), status


@bp.route("/damage-reports", methods=["POST"])
def create_damage_report():
    body = request.get_json(silent=True) or {}
    rental_id = body.get("rentalId", "")
    vehicle_id = body.get("vehicleId", "")
    description = body.get("description", "")
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))

    log.info("POST /damage-reports rentalId=%s vehicleId=%s corr=%s", rental_id, vehicle_id, correlation_id)

    if not rental_id:
        return _err("DAMAGE_RENTAL_REQUIRED", "rentalId is required", 422)
    if not vehicle_id:
        return _err("DAMAGE_VEHICLE_REQUIRED", "vehicleId is required", 422)
    if not description:
        return _err("DAMAGE_DESCRIPTION_REQUIRED", "description is required", 422)

    ok, code, http_status, msg = flk_rental_exists(rental_id)
    if not ok:
        return _err(code, msg, http_status)

    rental = store.db["rental_sessions"][rental_id]
    if rental["status"] not in (RentalStatus.FINISHED, RentalStatus.PAID, RentalStatus.PAYMENT_FAILED):
        return _err(
            "RENTAL_NOT_CLOSED",
            f"Damage report can only be created for a finished rental (current: {rental['status']})",
            409,
        )

    report_id = f"dmg-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    severity = body.get("severity", "MEDIUM")
    if severity not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        return _err("DAMAGE_SEVERITY_INVALID", "severity must be LOW, MEDIUM, HIGH or CRITICAL", 422)

    report = {
        "reportId": report_id,
        "rentalId": rental_id,
        "vehicleId": vehicle_id,
        "userId": rental.get("userId"),
        "description": description,
        "severity": severity,
        "photoUrls": body.get("photoUrls", []),
        "status": DamageReportStatus.CREATED,
        "created_at": now,
    }
    store.db["damage_reports"][report_id] = report
    store.add_outbox("DAMAGE_REPORT_CREATED", {"reportId": report_id, "rentalId": rental_id, "vehicleId": vehicle_id, "severity": severity})
    store.add_audit("damage_report", report_id, "N/A", DamageReportStatus.CREATED, description[:80], "api", correlation_id)

    log.info("Damage report created: %s rentalId=%s vehicle=%s severity=%s", report_id, rental_id, vehicle_id, severity)
    return jsonify(report), 201


@bp.route("/damage-reports/<report_id>", methods=["GET"])
def get_damage_report(report_id: str):
    log.info("GET /damage-reports/%s", report_id)
    r = store.db["damage_reports"].get(report_id)
    if not r:
        return jsonify({"error": "DAMAGE_REPORT_NOT_FOUND", "message": f"Damage report '{report_id}' not found"}), 404
    return jsonify(r), 200
