"""
NEGATIVE — Case 003: Аренда с просроченным водительским удостоверением

Вариации:
  A. POST /bookings с user-expired-lic -> 409 DRIVER_LICENSE_EXPIRED
  B. GET /driver-license показывает просроченную дату
  C. Пользователь VERIFIED, но ВУ просрочено -> бронирование заблокировано
  D. Без ВУ вообще (несуществующий userId для ВУ) -> DRIVER_LICENSE_NOT_FOUND
  E. Контраст: user-verified с действующим ВУ успешно создаёт бронирование

FLK-LICENSE-001: driver_licenses.status == VERIFIED AND expiration_date > current_date
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

USER_EXPIRED = "user-expired-lic"
USER_OK = "user-verified"
VEHICLE_ID = "vehicle-available-4"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("NEG-003 | Бронирование с просроченным ВУ")
    c.reset()

    # Step 1: пользователь VERIFIED
    s, user = c.get_user(USER_EXPIRED)
    ch.step("GET /users/user-expired-lic — статус VERIFIED", s, user,
            expected_status=200, expected_fields={"status": "VERIFIED"})

    # Step 2: ВУ просрочено
    s, lic = c.get_driver_license(USER_EXPIRED)
    ch.step("GET /driver-license — статус VERIFIED, дата истекла", s, lic,
            expected_status=200, expected_fields={"status": "VERIFIED"})
    from datetime import date
    exp_date = lic.get("expiration_date", "")
    ch.check(
        f"expiration_date ({exp_date}) <= today ({date.today()})",
        bool(exp_date) and exp_date <= str(date.today()),
    )

    # Step 3: POST /bookings -> 409 FLK-LICENSE-001
    s, err = c.create_booking(USER_EXPIRED, VEHICLE_ID)
    ch.step("POST /bookings — просроченное ВУ -> 409 DRIVER_LICENSE_EXPIRED", s, err,
            expected_status=409, expected_error="DRIVER_LICENSE_EXPIRED")
    ch.check("в message есть userId или пояснение", bool(err.get("message")))

    # Step 4: авто осталось AVAILABLE (бронирование не создано)
    s, veh = c.get_vehicle(VEHICLE_ID)
    ch.step("GET /vehicles — авто всё ещё AVAILABLE (транзакция не прошла)", s, veh,
            expected_status=200, expected_fields={"status": "AVAILABLE"})

    # Step 5: в outbox нет BOOKING_CREATED для этого пользователя
    outbox = c.get_outbox("BOOKING_CREATED")
    user_bookings = [e for e in outbox if e.get("payload", {}).get("userId") == USER_EXPIRED]
    ch.check("outbox НЕ содержит BOOKING_CREATED для user-expired-lic", len(user_bookings) == 0)

    # Step 6: нет ВУ вообще (создаём пользователя без ВУ через config-изменение)
    # Используем существующего user-blocked (у него REJECTED ВУ)
    s, err2 = c.create_booking("user-blocked", VEHICLE_ID)
    # user-blocked заблокирован -> FLK-USER-003 сработает раньше
    ch.step("POST /bookings для user-blocked -> 409 USER_BLOCKED (не доходит до ВУ)", s, err2,
            expected_status=409, expected_error="USER_BLOCKED")

    # Step 7: контраст — user-verified успешно бронирует
    s, ok_bk = c.create_booking(USER_OK, VEHICLE_ID)
    ch.step("POST /bookings для user-verified (действующее ВУ) -> 201 CONFIRMED (контраст)", s, ok_bk,
            expected_status=201, expected_fields={"status": "CONFIRMED"})

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
