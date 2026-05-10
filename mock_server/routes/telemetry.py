from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request
from ..state import store
from ..logger_config import get_logger

bp = Blueprint("telemetry", __name__, url_prefix="/v1")
log = get_logger()

TELEMETRY_STALE_THRESHOLD_MINUTES = 10


@bp.route("/vehicles/<vehicle_id>/telemetry", methods=["GET"])
def get_telemetry(vehicle_id: str):
    log.info("GET /vehicles/%s/telemetry", vehicle_id)

    if vehicle_id not in store.db["vehicles"]:
        return jsonify({"error": "VEHICLE_NOT_FOUND", "message": f"Vehicle '{vehicle_id}' not found"}), 404

    # Case 011: telemetry service unavailable
    if not store.mock_config.get("telemetry_service_available", True):
        return jsonify({"error": "TELEMETRY_SERVICE_UNAVAILABLE", "message": "Telemetry service is currently unavailable"}), 503

    telem = store.db["vehicle_telemetry"].get(vehicle_id)
    if telem is None:
        return jsonify({
            "vehicleId": vehicle_id,
            "available": False,
            "stale": True,
            "message": "No telemetry data available",
        }), 200

    # Case 011: simulated lost telemetry
    if vehicle_id in store.mock_config.get("telemetry_lost_vehicles", []):
        return jsonify({
            "vehicleId": vehicle_id,
            "available": False,
            "stale": True,
            "last_known": telem,
            "message": "Telemetry signal lost",
        }), 200

    result = dict(telem)
    result["stale"] = False
    result["available"] = True

    log.debug("Telemetry returned: vehicle=%s stale=False", vehicle_id)
    return jsonify(result), 200


@bp.route("/vehicles/<vehicle_id>/telemetry", methods=["PUT"])
def update_telemetry(vehicle_id: str):
    """Allow AI agent to simulate telemetry updates during a rental."""
    body = request.get_json(silent=True) or {}
    log.info("PUT /vehicles/%s/telemetry", vehicle_id)

    if vehicle_id not in store.db["vehicles"]:
        return jsonify({"error": "VEHICLE_NOT_FOUND", "message": f"Vehicle '{vehicle_id}' not found"}), 404

    telem = store.db["vehicle_telemetry"].get(vehicle_id, {"vehicleId": vehicle_id})
    telem.update({
        k: v for k, v in body.items()
        if k in ("lat", "lon", "speed_kmh", "fuel_level", "engine_on", "doors_closed")
    })
    telem["updated_at"] = datetime.now(timezone.utc).isoformat()
    telem["stale"] = False
    store.db["vehicle_telemetry"][vehicle_id] = telem

    log.info("Telemetry updated: vehicle=%s", vehicle_id)
    return jsonify(telem), 200
