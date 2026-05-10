from flask import Blueprint, jsonify, request
from ..state import store
from ..logger_config import get_logger

bp = Blueprint("users", __name__, url_prefix="/v1")
log = get_logger()


@bp.route("/users/<user_id>", methods=["GET"])
def get_user(user_id: str):
    log.info("GET /users/%s", user_id)
    user = store.db["users"].get(user_id)
    if not user:
        log.warning("User not found: %s", user_id)
        return jsonify({"error": "USER_NOT_FOUND", "message": f"User '{user_id}' not found"}), 404
    log.debug("User found: %s status=%s", user_id, user["status"])
    return jsonify(user), 200


@bp.route("/users/<user_id>/driver-license", methods=["GET"])
def get_driver_license(user_id: str):
    log.info("GET /users/%s/driver-license", user_id)
    if user_id not in store.db["users"]:
        return jsonify({"error": "USER_NOT_FOUND", "message": f"User '{user_id}' not found"}), 404
    lic = store.db["driver_licenses"].get(user_id)
    if not lic:
        log.warning("Driver license not found for user: %s", user_id)
        return jsonify({"error": "DRIVER_LICENSE_NOT_FOUND", "message": "No driver license found"}), 404
    log.debug("Driver license found: user=%s status=%s expiry=%s", user_id, lic["status"], lic["expiration_date"])
    return jsonify(lic), 200
