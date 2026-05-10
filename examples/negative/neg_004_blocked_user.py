"""
NEGATIVE — Case 004: Попытка аренды заблокированным пользователем

Вариации:
  A. POST /bookings -> 409 USER_BLOCKED
  B. GET /users возвращает BLOCKED корректно
  C. Попытка старта аренды напрямую (если бы booking был) -> тоже 409
  D. Проверка что авто не меняет статус при блокировке
  E. Контраст: user-verified успешно бронирует то же авто

FLK-USER-003: users.status != BLOCKED
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

USER_BLOCKED = "user-blocked"
USER_OK = "user-verified"
VEHICLE_ID = "vehicle-available-4"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("NEG-004 | Бронирование заблокированным пользователем")
    c.reset()

    # Step 1: пользователь BLOCKED
    s, user = c.get_user(USER_BLOCKED)
    ch.step("GET /users/user-blocked — статус BLOCKED", s, user,
            expected_status=200, expected_fields={"status": "BLOCKED"})

    # Step 2: POST /bookings -> 409 FLK-USER-003
    s, err = c.create_booking(USER_BLOCKED, VEHICLE_ID)
    ch.step("POST /bookings от заблокированного пользователя -> 409 USER_BLOCKED", s, err,
            expected_status=409, expected_error="USER_BLOCKED")

    # Step 3: авто не изменило статус
    s, veh = c.get_vehicle(VEHICLE_ID)
    ch.step("GET /vehicles — авто AVAILABLE (статус не изменился)", s, veh,
            expected_status=200, expected_fields={"status": "AVAILABLE"})

    # Step 4: outbox пуст для этого пользователя
    outbox = c.get_outbox("BOOKING_CREATED")
    blocked_bookings = [e for e in outbox if e.get("payload", {}).get("userId") == USER_BLOCKED]
    ch.check("outbox НЕ содержит BOOKING_CREATED для user-blocked", len(blocked_bookings) == 0)

    # Step 5: несуществующий пользователь -> 404
    s, err2 = c.create_booking("user-nonexistent", VEHICLE_ID)
    ch.step("POST /bookings для несуществующего userId -> 404 USER_NOT_FOUND", s, err2,
            expected_status=404, expected_error="USER_NOT_FOUND")

    # Step 6: GET несуществующего пользователя -> 404
    s, err3 = c.get_user("user-nonexistent")
    ch.step("GET /users/user-nonexistent -> 404 USER_NOT_FOUND", s, err3,
            expected_status=404, expected_error="USER_NOT_FOUND")

    # Step 7: контраст — user-verified успешно бронирует то же авто
    s, ok_bk = c.create_booking(USER_OK, VEHICLE_ID)
    ch.step("POST /bookings для user-verified -> 201 CONFIRMED (контраст)", s, ok_bk,
            expected_status=201, expected_fields={"status": "CONFIRMED"})

    # Step 8: теперь авто RESERVED
    s, veh2 = c.get_vehicle(VEHICLE_ID)
    ch.step("GET /vehicles — авто RESERVED после успешного бронирования", s, veh2,
            expected_status=200, expected_fields={"status": "RESERVED"})

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
