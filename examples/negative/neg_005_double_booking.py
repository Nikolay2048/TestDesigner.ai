"""
NEGATIVE — Case 005: Двойное бронирование одного автомобиля

Вариации:
  A. Авто уже RESERVED (pre-seeded) -> 409 VEHICLE_NOT_AVAILABLE
  B. Авто в IN_USE -> 409 VEHICLE_NOT_AVAILABLE
  C. Авто в MAINTENANCE -> 409 VEHICLE_NOT_AVAILABLE
  D. Авто в BLOCKED -> 409 VEHICLE_NOT_AVAILABLE
  E. Два пользователя одновременно бронируют одно авто — второй проигрывает
  F. После отмены первого бронирования авто снова доступно

FLK-VEHICLE-001: vehicles.status == AVAILABLE
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

USER_1 = "user-verified"
USER_2 = "user-for-cancel"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("NEG-005 | Двойное бронирование (VEHICLE_NOT_AVAILABLE)")
    c.reset()

    # ---- Вариант A: авто уже RESERVED ----
    RESERVED_VEHICLE = "vehicle-reserved"
    s, veh = c.get_vehicle(RESERVED_VEHICLE)
    ch.step("GET /vehicles/vehicle-reserved — статус RESERVED", s, veh,
            expected_status=200, expected_fields={"status": "RESERVED"})

    s, err = c.create_booking(USER_1, RESERVED_VEHICLE)
    ch.step("POST /bookings на RESERVED авто -> 409 VEHICLE_NOT_AVAILABLE", s, err,
            expected_status=409, expected_error="VEHICLE_NOT_AVAILABLE")

    # ---- Вариант B: авто IN_USE ----
    IN_USE_VEHICLE = "vehicle-in-use"
    s, err2 = c.create_booking(USER_1, IN_USE_VEHICLE)
    ch.step("POST /bookings на IN_USE авто -> 409 VEHICLE_NOT_AVAILABLE", s, err2,
            expected_status=409, expected_error="VEHICLE_NOT_AVAILABLE")

    # ---- Вариант C: авто MAINTENANCE ----
    s, err3 = c.create_booking(USER_1, "vehicle-maintenance")
    ch.step("POST /bookings на MAINTENANCE авто -> 409 VEHICLE_NOT_AVAILABLE", s, err3,
            expected_status=409, expected_error="VEHICLE_NOT_AVAILABLE")

    # ---- Вариант D: авто BLOCKED ----
    s, err4 = c.create_booking(USER_1, "vehicle-blocked")
    ch.step("POST /bookings на BLOCKED авто -> 409 VEHICLE_NOT_AVAILABLE", s, err4,
            expected_status=409, expected_error="VEHICLE_NOT_AVAILABLE")

    # ---- Вариант E: два пользователя — одно авто ----
    FREE_VEHICLE = "vehicle-available-4"

    # Первый пользователь бронирует
    s, bk1 = c.create_booking(USER_1, FREE_VEHICLE)
    ch.step(f"POST /bookings — USER_1 бронирует {FREE_VEHICLE} -> 201", s, bk1,
            expected_status=201, expected_fields={"status": "CONFIRMED"})
    bk1_id = bk1.get("bookingId")

    # Второй пользователь пытается забронировать то же авто
    s, err5 = c.create_booking(USER_2, FREE_VEHICLE)
    ch.step(f"POST /bookings — USER_2 пытается то же авто -> 409 VEHICLE_NOT_AVAILABLE", s, err5,
            expected_status=409, expected_error="VEHICLE_NOT_AVAILABLE")

    # ---- Вариант F: после отмены первого бронирования авто снова доступно ----
    s, cancelled = c.cancel_booking(bk1_id)
    ch.step(f"POST /bookings/{bk1_id}/cancel -> CANCELLED", s, cancelled,
            expected_status=200, expected_fields={"status": "CANCELLED"})

    s, veh_free = c.get_vehicle(FREE_VEHICLE)
    ch.step(f"GET /vehicles/{FREE_VEHICLE} — AVAILABLE после отмены", s, veh_free,
            expected_status=200, expected_fields={"status": "AVAILABLE"})

    s, bk2 = c.create_booking(USER_2, FREE_VEHICLE)
    ch.step("POST /bookings — USER_2 теперь успешно бронирует освободившееся авто -> 201", s, bk2,
            expected_status=201, expected_fields={"status": "CONFIRMED"})

    # Контроль outbox — нет BOOKING_CREATED для заблокированных попыток
    all_bookings_created = c.get_outbox("BOOKING_CREATED")
    # Успешных должно быть ровно 2: bk1 и bk2
    ch.check("outbox содержит 2 успешных BOOKING_CREATED", len(all_bookings_created) == 2)

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
