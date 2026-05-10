"""
POSITIVE — Case 001: Полный успешный флоу аренды автомобиля

Шаги:
  1. GET  /users/{userId}                    -> 200 VERIFIED
  2. GET  /users/{userId}/driver-license     -> 200 VERIFIED, не просрочено
  3. GET  /vehicles/available                -> 200, список > 0
  4. GET  /vehicles/{vehicleId}              -> 200 AVAILABLE
  5. POST /bookings                          -> 201 CONFIRMED
  6. GET  /bookings/{bookingId}              -> 200 CONFIRMED
  7. POST /rentals/start                     -> 201 STARTED
  8. GET  /rentals/{rentalId}               -> 200 STARTED + preliminary_amount
  9. PUT  /vehicles/{vehicleId}/telemetry   -> engine_on=False, doors_closed=True
 10. POST /rentals/{rentalId}/finish        -> 200 FINISHED
 11. POST /payments                         -> 201 CAPTURED
 12. GET  /payments/{paymentId}             -> 200 CAPTURED
 13. Проверка outbox-событий                -> BOOKING_CREATED, RENTAL_STARTED, RENTAL_FINISHED, PAYMENT_CAPTURED
 14. Проверка audit-лога                    -> переходы статусов зафиксированы
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

USER_ID = "user-verified"
VEHICLE_ID = "vehicle-available-4"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("POS-001 | Полный флоу аренды")
    c.reset()

    # Step 1: пользователь существует и VERIFIED
    s, body = c.get_user(USER_ID)
    ch.step("GET /users — пользователь найден и VERIFIED", s, body,
            expected_status=200, expected_fields={"status": "VERIFIED"})

    # Step 2: ВУ действительно и не просрочено
    s, lic = c.get_driver_license(USER_ID)
    ch.step("GET /driver-license — VERIFIED, не просрочено", s, lic,
            expected_status=200, expected_fields={"status": "VERIFIED"})
    ch.check("expiration_date указана", bool(lic.get("expiration_date")))

    # Step 3: доступные авто есть
    s, avail = c.get_vehicles_available()
    ch.step("GET /vehicles/available — есть автомобили", s, avail, expected_status=200)
    ch.check("список не пустой", avail.get("count", 0) > 0, f"count={avail.get('count')}")

    # Step 4: конкретный автомобиль AVAILABLE
    s, veh = c.get_vehicle(VEHICLE_ID)
    ch.step(f"GET /vehicles/{VEHICLE_ID} — AVAILABLE", s, veh,
            expected_status=200, expected_fields={"status": "AVAILABLE"})

    # Step 5: создание бронирования
    s, booking = c.create_booking(USER_ID, VEHICLE_ID)
    ch.step("POST /bookings — 201 CONFIRMED", s, booking,
            expected_status=201, expected_fields={"status": "CONFIRMED"})
    booking_id = booking.get("bookingId", "")
    ch.check("bookingId получен", bool(booking_id))

    # Step 6: авто перешло в RESERVED
    s, veh2 = c.get_vehicle(VEHICLE_ID)
    ch.step(f"GET /vehicles/{VEHICLE_ID} — стал RESERVED после бронирования", s, veh2,
            expected_status=200, expected_fields={"status": "RESERVED"})

    # Step 7: GET бронирования
    s, bk_check = c.get_booking(booking_id)
    ch.step(f"GET /bookings/{booking_id} — CONFIRMED", s, bk_check,
            expected_status=200, expected_fields={"status": "CONFIRMED"})

    # Step 8: старт аренды
    s, rental = c.start_rental(booking_id, user_location={"lat": 55.76, "lon": 37.63})
    ch.step("POST /rentals/start — 201 STARTED", s, rental,
            expected_status=201, expected_fields={"status": "STARTED"})
    rental_id = rental.get("rentalId", "")
    ch.check("rentalId получен", bool(rental_id))

    # Step 9: бронирование перешло в STARTED
    s, bk3 = c.get_booking(booking_id)
    ch.step(f"GET /bookings/{booking_id} — стал STARTED", s, bk3,
            expected_status=200, expected_fields={"status": "STARTED"})

    # Step 10: авто перешло в IN_USE
    s, veh3 = c.get_vehicle(VEHICLE_ID)
    ch.step(f"GET /vehicles/{VEHICLE_ID} — стал IN_USE", s, veh3,
            expected_status=200, expected_fields={"status": "IN_USE"})

    # Step 11: GET аренды — видна предварительная стоимость
    s, rent_check = c.get_rental(rental_id)
    ch.step(f"GET /rentals/{rental_id} — STARTED + preliminary_amount", s, rent_check,
            expected_status=200, expected_fields={"status": "STARTED"})
    ch.check("preliminary_amount >= 0", rent_check.get("preliminary_amount", -1) >= 0)

    # Step 12: обновить телеметрию — авто стоит, двери закрыты, двигатель выкл
    s, telem = c.update_telemetry(VEHICLE_ID, engine_on=False, doors_closed=True, speed_kmh=0)
    ch.step("PUT /telemetry — engine_on=False, doors_closed=True, speed=0", s, telem,
            expected_status=200)

    # Step 13: завершение аренды
    s, finished = c.finish_rental(rental_id)
    ch.step(f"POST /rentals/{rental_id}/finish — 200 FINISHED", s, finished,
            expected_status=200, expected_fields={"status": "FINISHED"})
    ch.check("amount > 0", finished.get("amount", 0) > 0, f"amount={finished.get('amount')}")

    # Step 14: авто вернулось в AVAILABLE
    s, veh4 = c.get_vehicle(VEHICLE_ID)
    ch.step(f"GET /vehicles/{VEHICLE_ID} — вернулся в AVAILABLE", s, veh4,
            expected_status=200, expected_fields={"status": "AVAILABLE"})

    # Step 15: создание платежа
    amount = finished.get("amount", 10.0)
    s, payment = c.create_payment(rental_id, amount)
    ch.step("POST /payments — 201 CAPTURED", s, payment,
            expected_status=201, expected_fields={"status": "CAPTURED"})
    payment_id = payment.get("paymentId", "")

    # Step 16: GET платежа
    s, pay_check = c.get_payment(payment_id)
    ch.step(f"GET /payments/{payment_id} — CAPTURED", s, pay_check,
            expected_status=200, expected_fields={"status": "CAPTURED"})

    # Step 17: аренда в статусе PAID
    s, rent_final = c.get_rental(rental_id)
    ch.step(f"GET /rentals/{rental_id} — статус PAID", s, rent_final,
            expected_status=200, expected_fields={"status": "PAID"})

    # Step 18: outbox-события
    outbox = c.get_outbox()
    event_types = {e["event_type"] for e in outbox}
    for expected_event in ("BOOKING_CREATED", "RENTAL_STARTED", "RENTAL_FINISHED", "PAYMENT_CAPTURED"):
        ch.check(f"outbox содержит {expected_event}", expected_event in event_types)

    # Step 19: audit-лог для booking
    audit = c.get_audit(entity_type="booking", entity_id=booking_id)
    ch.check("audit содержит запись по booking", len(audit) > 0)

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
