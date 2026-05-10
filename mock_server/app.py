import uuid
from flask import Flask, jsonify, request, g
from .logger_config import get_logger
from .seed import seed_all

log = get_logger()


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False

    # ------------------------------------------------------------------ #
    #  Request / response logging                                         #
    # ------------------------------------------------------------------ #

    @app.before_request
    def before_request():
        g.correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        log.info(">>> %s %s  corr=%s  body=%s",
                 request.method, request.path, g.correlation_id,
                 request.get_data(as_text=True)[:500] if request.content_length else "")

    @app.after_request
    def after_request(response):
        response.headers["X-Correlation-ID"] = getattr(g, "correlation_id", "")
        log.info("<<< %s %s  status=%d  corr=%s",
                 request.method, request.path, response.status_code,
                 getattr(g, "correlation_id", ""))
        return response

    # ------------------------------------------------------------------ #
    #  Blueprints                                                         #
    # ------------------------------------------------------------------ #

    from .routes.users import bp as users_bp
    from .routes.vehicles import bp as vehicles_bp
    from .routes.bookings import bp as bookings_bp
    from .routes.rentals import bp as rentals_bp
    from .routes.payments import bp as payments_bp
    from .routes.fines import bp as fines_bp
    from .routes.damage_reports import bp as damage_reports_bp
    from .routes.telemetry import bp as telemetry_bp
    from .routes.mock_control import bp as mock_control_bp

    for bp in (users_bp, vehicles_bp, bookings_bp, rentals_bp, payments_bp,
               fines_bp, damage_reports_bp, telemetry_bp, mock_control_bp):
        app.register_blueprint(bp)

    # ------------------------------------------------------------------ #
    #  Global error handlers                                              #
    # ------------------------------------------------------------------ #

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "NOT_FOUND", "message": str(e)}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "METHOD_NOT_ALLOWED", "message": str(e)}), 405

    @app.errorhandler(Exception)
    def unhandled(e):
        log.exception("Unhandled exception: %s", e)
        return jsonify({"error": "INTERNAL_ERROR", "message": str(e)}), 500

    # ------------------------------------------------------------------ #
    #  Health & root                                                       #
    # ------------------------------------------------------------------ #

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "carsharing-mock"}), 200

    @app.route("/", methods=["GET"])
    def root():
        return jsonify({
            "service": "Carsharing Mock Server",
            "version": "1.0.0",
            "docs": {
                "seed_ids": "/mock/seed-ids",
                "state": "/mock/state",
                "config": "/mock/config",
                "reset": "POST /mock/reset",
                "health": "/health",
            },
            "api_prefix": "/v1",
        }), 200

    # ------------------------------------------------------------------ #
    #  Seed on startup                                                     #
    # ------------------------------------------------------------------ #
    seed_all()
    log.info("Carsharing mock server initialized with seed data")

    return app
