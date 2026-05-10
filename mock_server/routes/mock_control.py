"""
/mock/* — control endpoints for the AI agent to configure mock behaviour.

These endpoints have NO business validation — they are test infrastructure.
"""

from flask import Blueprint, jsonify, request
from ..state import store
from ..seed import seed_all
from ..logger_config import get_logger

bp = Blueprint("mock_control", __name__, url_prefix="/mock")
log = get_logger()


# ------------------------------------------------------------------ #
#  Reset / Seed                                                        #
# ------------------------------------------------------------------ #

@bp.route("/reset", methods=["POST"])
def reset():
    """Restore to initial seed state."""
    log.info("MOCK /mock/reset — restoring initial state")
    store.restore_snapshot()
    return jsonify({"result": "ok", "message": "State restored to initial seed"}), 200


@bp.route("/reseed", methods=["POST"])
def reseed():
    """Wipe everything and re-seed from scratch."""
    log.info("MOCK /mock/reseed — full reseed")
    for key in store.db:
        if isinstance(store.db[key], dict):
            store.db[key].clear()
        elif isinstance(store.db[key], list):
            store.db[key].clear()
    seed_all()
    return jsonify({"result": "ok", "message": "State fully reseeded"}), 200


# ------------------------------------------------------------------ #
#  State inspection                                                    #
# ------------------------------------------------------------------ #

@bp.route("/state", methods=["GET"])
def get_state():
    """Return full in-memory state (for debugging)."""
    collection = request.args.get("collection")
    if collection:
        data = store.db.get(collection)
        if data is None:
            return jsonify({"error": "COLLECTION_NOT_FOUND", "available": list(store.db.keys())}), 404
        return jsonify(data), 200
    # Return summary to avoid giant response
    summary = {k: (len(v) if isinstance(v, (dict, list)) else v) for k, v in store.db.items()}
    return jsonify({"collections": summary, "mock_config": store.mock_config}), 200


@bp.route("/config", methods=["GET"])
def get_config():
    return jsonify(store.mock_config), 200


@bp.route("/config", methods=["POST", "PATCH"])
def update_config():
    body = request.get_json(silent=True) or {}
    log.info("MOCK /mock/config update: %s", body)
    for key, value in body.items():
        if key in store.mock_config:
            store.mock_config[key] = value
        else:
            log.warning("Unknown mock_config key: %s", key)
    return jsonify({"result": "ok", "mock_config": store.mock_config}), 200


# ------------------------------------------------------------------ #
#  Zone control (case 006)                                            #
# ------------------------------------------------------------------ #

@bp.route("/vehicles/<vehicle_id>/set-outside-zone", methods=["POST"])
def set_outside_zone(vehicle_id: str):
    if vehicle_id not in store.mock_config["vehicles_outside_zone"]:
        store.mock_config["vehicles_outside_zone"].append(vehicle_id)
    log.info("MOCK vehicle %s marked as OUTSIDE zone", vehicle_id)
    return jsonify({"result": "ok", "vehicleId": vehicle_id, "outside_zone": True}), 200


@bp.route("/vehicles/<vehicle_id>/set-inside-zone", methods=["POST"])
def set_inside_zone(vehicle_id: str):
    store.mock_config["vehicles_outside_zone"] = [
        v for v in store.mock_config["vehicles_outside_zone"] if v != vehicle_id
    ]
    log.info("MOCK vehicle %s marked as INSIDE zone", vehicle_id)
    return jsonify({"result": "ok", "vehicleId": vehicle_id, "outside_zone": False}), 200


# ------------------------------------------------------------------ #
#  Payment control (cases 007, 008)                                   #
# ------------------------------------------------------------------ #

@bp.route("/payments/<payment_id>/set-fail-capture", methods=["POST"])
def set_fail_capture(payment_id: str):
    if payment_id not in store.mock_config["payment_capture_fails"]:
        store.mock_config["payment_capture_fails"].append(payment_id)
    log.info("MOCK payment %s will FAIL on capture", payment_id)
    return jsonify({"result": "ok", "paymentId": payment_id, "fail_capture": True}), 200


@bp.route("/payments/<payment_id>/set-success-capture", methods=["POST"])
def set_success_capture(payment_id: str):
    store.mock_config["payment_capture_fails"] = [
        p for p in store.mock_config["payment_capture_fails"] if p != payment_id
    ]
    log.info("MOCK payment %s will SUCCEED on capture", payment_id)
    return jsonify({"result": "ok", "paymentId": payment_id, "fail_capture": False}), 200


# ------------------------------------------------------------------ #
#  Telemetry loss control (case 011)                                  #
# ------------------------------------------------------------------ #

@bp.route("/telemetry/<vehicle_id>/set-lost", methods=["POST"])
def set_telemetry_lost(vehicle_id: str):
    if vehicle_id not in store.mock_config["telemetry_lost_vehicles"]:
        store.mock_config["telemetry_lost_vehicles"].append(vehicle_id)
    log.info("MOCK telemetry LOST for vehicle %s", vehicle_id)
    return jsonify({"result": "ok", "vehicleId": vehicle_id, "telemetry_lost": True}), 200


@bp.route("/telemetry/<vehicle_id>/set-restored", methods=["POST"])
def set_telemetry_restored(vehicle_id: str):
    store.mock_config["telemetry_lost_vehicles"] = [
        v for v in store.mock_config["telemetry_lost_vehicles"] if v != vehicle_id
    ]
    log.info("MOCK telemetry RESTORED for vehicle %s", vehicle_id)
    return jsonify({"result": "ok", "vehicleId": vehicle_id, "telemetry_lost": False}), 200


# ------------------------------------------------------------------ #
#  Service availability toggles                                       #
# ------------------------------------------------------------------ #

_SERVICE_KEYS = {
    "geo": "geo_service_available",
    "payment": "payment_provider_available",
    "telemetry": "telemetry_service_available",
    "notification": "notification_service_available",
}


@bp.route("/services/<service>/disable", methods=["POST"])
def disable_service(service: str):
    key = _SERVICE_KEYS.get(service)
    if not key:
        return jsonify({"error": "UNKNOWN_SERVICE", "available": list(_SERVICE_KEYS.keys())}), 404
    store.mock_config[key] = False
    log.info("MOCK service DISABLED: %s", service)
    return jsonify({"result": "ok", "service": service, "available": False}), 200


@bp.route("/services/<service>/enable", methods=["POST"])
def enable_service(service: str):
    key = _SERVICE_KEYS.get(service)
    if not key:
        return jsonify({"error": "UNKNOWN_SERVICE", "available": list(_SERVICE_KEYS.keys())}), 404
    store.mock_config[key] = True
    log.info("MOCK service ENABLED: %s", service)
    return jsonify({"result": "ok", "service": service, "available": True}), 200


# ------------------------------------------------------------------ #
#  Rental finish block (case with critical technical event)           #
# ------------------------------------------------------------------ #

@bp.route("/rentals/<rental_id>/block-finish", methods=["POST"])
def block_rental_finish(rental_id: str):
    if rental_id not in store.mock_config["rentals_blocked_finish"]:
        store.mock_config["rentals_blocked_finish"].append(rental_id)
    log.info("MOCK rental finish BLOCKED: %s", rental_id)
    return jsonify({"result": "ok", "rentalId": rental_id, "finish_blocked": True}), 200


@bp.route("/rentals/<rental_id>/unblock-finish", methods=["POST"])
def unblock_rental_finish(rental_id: str):
    store.mock_config["rentals_blocked_finish"] = [
        r for r in store.mock_config["rentals_blocked_finish"] if r != rental_id
    ]
    log.info("MOCK rental finish UNBLOCKED: %s", rental_id)
    return jsonify({"result": "ok", "rentalId": rental_id, "finish_blocked": False}), 200


# ------------------------------------------------------------------ #
#  Outbox / Audit inspection                                          #
# ------------------------------------------------------------------ #

@bp.route("/outbox", methods=["GET"])
def get_outbox():
    event_type = request.args.get("type")
    events = store.db["outbox_events"]
    if event_type:
        events = [e for e in events if e["event_type"] == event_type]
    return jsonify({"count": len(events), "events": events}), 200


@bp.route("/audit", methods=["GET"])
def get_audit():
    entity_type = request.args.get("entity_type")
    entity_id = request.args.get("entity_id")
    logs = store.db["audit_logs"]
    if entity_type:
        logs = [l for l in logs if l["entity_type"] == entity_type]
    if entity_id:
        logs = [l for l in logs if l["entity_id"] == entity_id]
    return jsonify({"count": len(logs), "logs": logs}), 200


# ------------------------------------------------------------------ #
#  Seed data IDs reference                                            #
# ------------------------------------------------------------------ #

@bp.route("/seed-ids", methods=["GET"])
def seed_ids():
    """Return a reference map of all pre-seeded entity IDs for the AI agent."""
    return jsonify({
        "users": {
            "user-verified": "VERIFIED, valid license, active payment (happy path)",
            "user-expired-lic": "VERIFIED but expired driver license (case 003)",
            "user-blocked": "BLOCKED user (case 004)",
            "user-for-cancel": "VERIFIED, has booking-to-cancel (case 009)",
            "user-auto-cancel": "VERIFIED, has booking-to-expire (case 010)",
            "user-with-rental": "VERIFIED, has STARTED rental-started (case 011,012)",
            "user-for-refund": "VERIFIED, has PAID rental + CAPTURED payment (case 015)",
            "user-for-fine": "VERIFIED, has FINISHED rental-finished (case 013,014)",
            "user-for-retry": "VERIFIED, has PAYMENT_FAILED rental + FAILED payment (case 008)",
        },
        "vehicles": {
            "vehicle-available-1": "AVAILABLE — linked to booking-confirmed (user-verified)",
            "vehicle-available-2": "AVAILABLE — linked to booking-to-cancel",
            "vehicle-available-3": "AVAILABLE — linked to booking-to-expire",
            "vehicle-available-4": "AVAILABLE (was in booking-expired)",
            "vehicle-available-5": "AVAILABLE (used in finished rentals)",
            "vehicle-reserved": "RESERVED — linked to booking-reserved (case 005)",
            "vehicle-in-use": "IN_USE — has active rental-started",
            "vehicle-maintenance": "MAINTENANCE",
            "vehicle-blocked": "BLOCKED",
        },
        "bookings": {
            "booking-confirmed": "CONFIRMED, user-verified / vehicle-available-1",
            "booking-expired": "EXPIRED — for case 002 (try start rental → 409)",
            "booking-to-cancel": "CONFIRMED — for case 009 (cancel)",
            "booking-to-expire": "CONFIRMED (past TTL) — for case 010 (expire)",
            "booking-reserved": "CONFIRMED / vehicle-reserved — for case 005",
            "booking-started": "STARTED / vehicle-in-use — linked to rental-started",
        },
        "rentals": {
            "rental-started": "STARTED, user-with-rental / vehicle-in-use",
            "rental-finished": "FINISHED, user-for-fine (case 013,014)",
            "rental-for-refund": "PAID, user-for-refund (case 015)",
            "rental-for-retry": "PAYMENT_FAILED, user-for-retry (case 008)",
        },
        "payments": {
            "payment-failed": "FAILED, rental-for-retry (case 008 retry)",
            "payment-captured": "CAPTURED, rental-for-refund (case 015 refund)",
        },
        "mock_controls": {
            "POST /mock/vehicles/{id}/set-outside-zone": "Make vehicle outside finish zone (case 006)",
            "POST /mock/vehicles/{id}/set-inside-zone": "Restore vehicle to zone",
            "POST /mock/services/geo/disable": "Disable geo service",
            "POST /mock/services/payment/disable": "Disable payment provider (case 007)",
            "POST /mock/services/payment/enable": "Re-enable payment provider",
            "POST /mock/services/telemetry/disable": "Disable telemetry service",
            "POST /mock/telemetry/{id}/set-lost": "Simulate telemetry signal loss (case 011)",
            "POST /mock/telemetry/{id}/set-restored": "Restore telemetry signal",
            "POST /mock/rentals/{id}/block-finish": "Block rental finish with tech event",
            "POST /mock/reset": "Restore to initial seed state",
            "GET /mock/state": "Inspect full in-memory state",
            "GET /mock/outbox": "Inspect outbox events",
            "GET /mock/audit": "Inspect audit logs",
        },
    }), 200
