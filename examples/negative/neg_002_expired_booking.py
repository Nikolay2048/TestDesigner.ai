"""
NEGATIVE — Case 002: Попытка старта аренды по истёкшему бронированию

Вариации:
  A. Прямой старт по booking-expired -> 409 BOOKING_STATUS_INVALID
  B. GET бронирования возвращает EXPIRED корректно
  C. Booking с истёкшим TTL (но CONFIRMED) -> BOOKING_EXPIRED при старте
  D. Корректный флоу после создания нового бронирования

FLK: FLK-BOOKING-001 (статус CONFIRMED/STARTED), FLK-BOOKING-NOT-EXPIRED (TTL)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

EXPIRED_BOOKING_ID = "booking-expired"
PAST_TTL_BOOKING_ID = "booking-to-expire"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("NEG-002 | Старт аренды по истёкшему бронированию")
    c.reset()

    # ---- Вариант A: бронирование уже в статусе EXPIRED ----
    s, bk = c.get_booking(EXPIRED_BOOKING_ID)
    ch.step("GET /bookings/booking-expired — статус EXPIRED", s, bk,
            expected_status=200, expected_fields={"status": "EXPIRED"})

    s, err = c.start_rental(EXPIRED_BOOKING_ID)
    ch.step("POST /rentals/start по EXPIRED бронированию -> 409 BOOKING_STATUS_INVALID", s, err,
            expected_status=409, expected_error="BOOKING_STATUS_INVALID")
    ch.check("ошибка описана в message", bool(err.get("message")))

    # Убеждаемся, что аренда НЕ создалась
    state = c.get_state("rental_sessions")
    pre_count = len(state)
    s, err2 = c.start_rental(EXPIRED_BOOKING_ID)
    state2 = c.get_state("rental_sessions")
    ch.check("количество rental_sessions не изменилось", len(state2) == len(state))

    # ---- Вариант B: бронирование CONFIRMED, но TTL истёк ----
    s, bk2 = c.get_booking(PAST_TTL_BOOKING_ID)
    ch.step("GET /bookings/booking-to-expire — CONFIRMED (TTL истёк)", s, bk2,
            expected_status=200, expected_fields={"status": "CONFIRMED"})

    s, err3 = c.start_rental(PAST_TTL_BOOKING_ID)
    ch.step("POST /rentals/start по CONFIRMED с истёкшим TTL -> 409 BOOKING_EXPIRED", s, err3,
            expected_status=409, expected_error="BOOKING_EXPIRED")

    # ---- Вариант C: нормальный флоу — создаём новое бронирование и стартуем ----
    s, new_bk = c.create_booking("user-verified", "vehicle-available-4")
    ch.step("POST /bookings — новое бронирование -> 201", s, new_bk, expected_status=201)
    new_bid = new_bk.get("bookingId")

    s, rental = c.start_rental(new_bid)
    ch.step("POST /rentals/start по свежему бронированию -> 201 STARTED (контраст)", s, rental,
            expected_status=201, expected_fields={"status": "STARTED"})

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
