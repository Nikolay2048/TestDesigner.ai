from datetime import datetime
from flask import Flask, jsonify, request

app = Flask(__name__)

vehicles = {
    "vehicle-available-1": {"vehicleId": "vehicle-available-1", "status": "AVAILABLE"},
    "vehicle-booked-1": {"vehicleId": "vehicle-booked-1", "status": "BOOKED"},
}

bookings = {}


@app.get("/v1/vehicles/available")
def get_available_vehicles():
    available = [v for v in vehicles.values() if v["status"] == "AVAILABLE"]

    return jsonify({
        "count": len(available),
        "items": available
    }), 200


@app.post("/v1/bookings")
def create_booking():
    body = request.get_json(silent=True) or {}

    vehicle_id = body.get("vehicleId")
    start_date = body.get("startDate")
    user_id = body.get("userId", "user-1")

    if not vehicle_id:
        return jsonify({
            "error": "VEHICLE_ID_REQUIRED",
            "message": "vehicleId is required"
        }), 400

    if vehicle_id not in vehicles:
        return jsonify({
            "error": "VEHICLE_NOT_FOUND",
            "message": "Vehicle does not exist"
        }), 404

    if vehicles[vehicle_id]["status"] != "AVAILABLE":
        return jsonify({
            "error": "VEHICLE_NOT_AVAILABLE",
            "message": "Vehicle is not available"
        }), 409

    if not start_date:
        return jsonify({
            "error": "START_DATE_REQUIRED",
            "message": "startDate is required"
        }), 400

    try:
        parsed_start_date = datetime.fromisoformat(start_date)
    except ValueError:
        return jsonify({
            "error": "INVALID_START_DATE_FORMAT",
            "message": "startDate must be ISO datetime"
        }), 400

    if parsed_start_date <= datetime.now():
        return jsonify({
            "error": "INVALID_START_DATE",
            "message": "startDate must be in the future"
        }), 400

    booking_id = f"booking-{len(bookings) + 1}"

    bookings[booking_id] = {
        "bookingId": booking_id,
        "userId": user_id,
        "vehicleId": vehicle_id,
        "startDate": start_date,
        "status": "CREATED"
    }

    vehicles[vehicle_id]["status"] = "BOOKED"

    return jsonify(bookings[booking_id]), 201


@app.get("/v1/bookings/<booking_id>")
def get_booking(booking_id: str):
    if booking_id not in bookings:
        return jsonify({
            "error": "BOOKING_NOT_FOUND",
            "message": "Booking does not exist"
        }), 404

    return jsonify(bookings[booking_id]), 200


@app.post("/__reset")
def reset_state():
    vehicles["vehicle-available-1"]["status"] = "AVAILABLE"
    vehicles["vehicle-booked-1"]["status"] = "BOOKED"
    bookings.clear()

    return jsonify({"status": "RESET_DONE"}), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)