"""
POSITIVE — Case 009: Отмена бронирования пользователем

Сценарий:
  1. Подтверждённое бронирование существует -> CONFIRMED
  2. POST /bookings/{id}/cancel -> 200 CANCELLED
  3. Автомобиль освобождается -> AVAILABLE
  4. Повторная отмена отклоняется
  5. Попытка старта отменённой аренды отклоняется
  6. Проверка outbox — BOOKING_CANCELLED

Вариация: отмена с указанием причины vs без причины.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

BOOKING_ID = "booking-to-cancel"
VEHICLE_ID = "vehicle-available-2"
USER_ID = "user-for-cancel"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("POS-009 | Отмена бронирования пользователем")
    c.reset()

    # Step 1: бронирование в CONFIRMED
    s, bk = c.get_booking(BOOKING_ID)
    ch.step("GET /bookings — статус CONFIRMED", s, bk,
            expected_status=200, expected_fields={"status": "CONFIRMED"})

    # Step 2: авто в RESERVED
    s, veh = c.get_vehicle(VEHICLE_ID)
    ch.step("GET /vehicles — статус RESERVED", s, veh,
            expected_status=200, expected_fields={"status": "RESERVED"})

    # Step 3: отмена с указанием причины
    s, cancelled = c.cancel_booking(BOOKING_ID, reason="CHANGED_MIND")
    ch.step("POST /bookings/cancel с причиной CHANGED_MIND -> 200 CANCELLED", s, cancelled,
            expected_status=200, expected_fields={"status": "CANCELLED"})
    ch.check("cancel_reason сохранена", cancelled.get("cancel_reason") == "CHANGED_MIND")

    # Step 4: авто вернулось в AVAILABLE
    s, veh2 = c.get_vehicle(VEHICLE_ID)
    ch.step("GET /vehicles — вернулся в AVAILABLE", s, veh2,
            expected_status=200, expected_fields={"status": "AVAILABLE"})

    # Step 5: повторная отмена -> 409 (статус не CONFIRMED/STARTED)
    s, cancel2 = c.cancel_booking(BOOKING_ID)
    ch.step("POST /bookings/cancel повторно -> 409 BOOKING_STATUS_INVALID", s, cancel2,
            expected_status=409, expected_error="BOOKING_STATUS_INVALID")

    # Step 6: старт аренды по отменённому бронированию -> 409
    s, start_err = c.start_rental(BOOKING_ID)
    ch.step("POST /rentals/start по отменённому бронированию -> 409", s, start_err,
            expected_status=409, expected_error="BOOKING_STATUS_INVALID")

    # Step 7: новое бронирование этого же авто теперь возможно (авто AVAILABLE)
    s, new_bk = c.create_booking(USER_ID, VEHICLE_ID)
    ch.step("POST /bookings — авто снова доступно, новое бронирование -> 201", s, new_bk,
            expected_status=201, expected_fields={"status": "CONFIRMED"})

    # Step 8: outbox
    outbox_types = {e["event_type"] for e in c.get_outbox()}
    ch.check("outbox содержит BOOKING_CANCELLED", "BOOKING_CANCELLED" in outbox_types)
    ch.check("outbox содержит BOOKING_CREATED (для нового бронирования)",
             "BOOKING_CREATED" in outbox_types)

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
