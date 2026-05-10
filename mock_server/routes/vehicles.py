from flask import Blueprint, jsonify, request
from ..state import store
from ..models import VehicleStatus
from ..logger_config import get_logger

bp = Blueprint("vehicles", __name__, url_prefix="/v1")
log = get_logger()

_UNAVAILABLE_STATUSES = {
    VehicleStatus.IN_USE, VehicleStatus.MAINTENANCE,
    VehicleStatus.BLOCKED, VehicleStatus.OFFLINE, VehicleStatus.RESERVED,
}


@bp.route("/vehicles/available", methods=["GET"])
def get_available_vehicles():
    log.info("GET /vehicles/available")
    available = [
        v for v in store.db["vehicles"].values()
        if v["status"] == VehicleStatus.AVAILABLE
    ]
    log.debug("Available vehicles count: %d", len(available))
    return jsonify({"items": available, "count": len(available)}), 200


@bp.route("/vehicles/<vehicle_id>", methods=["GET"])
def get_vehicle(vehicle_id: str):
    log.info("GET /vehicles/%s", vehicle_id)
    v = store.db["vehicles"].get(vehicle_id)
    if not v:
        log.warning("Vehicle not found: %s", vehicle_id)
        return jsonify({"error": "VEHICLE_NOT_FOUND", "message": f"Vehicle '{vehicle_id}' not found"}), 404
    result = dict(v)
    result["available_for_booking"] = (v["status"] == VehicleStatus.AVAILABLE)
    log.debug("Vehicle found: %s status=%s", vehicle_id, v["status"])
    return jsonify(result), 200
