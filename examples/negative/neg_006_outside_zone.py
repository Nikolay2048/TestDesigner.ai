"""
NEGATIVE — Case 006: Завершение аренды вне разрешённой зоны

Сценарий:
  1. Активная аренда rental-started
  2. Помечаем авто как "вне зоны" через /mock/vehicles/{id}/set-outside-zone
  3. POST /rentals/{id}/finish -> 409 VEHICLE_OUTSIDE_ALLOWED_ZONE
  4. Аренда остаётся STARTED
  5. Возвращаем авто в зону -> finish проходит успешно
  6. Вариация: блокировка finish из-за критического технического события

FLK-ZONE: vehicle_id not in vehicles_outside_zone
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

RENTAL_ID = "rental-started"
VEHICLE_ID = "vehicle-in-use"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("NEG-006 | Завершение аренды вне разрешённой зоны")
    c.reset()

    # Step 1: аренда активна
    s, rental = c.get_rental(RENTAL_ID)
    ch.step("GET /rentals/rental-started — STARTED", s, rental,
            expected_status=200, expected_fields={"status": "STARTED"})

    # Step 2: помечаем авто как вне зоны
    c.set_outside_zone(VEHICLE_ID)
    cfg = c.get_config()
    ch.check(f"{VEHICLE_ID} добавлен в vehicles_outside_zone",
             VEHICLE_ID in cfg.get("vehicles_outside_zone", []))

    # Step 3: попытка завершить аренду -> 409
    s, err = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish (авто вне зоны) -> 409 VEHICLE_OUTSIDE_ALLOWED_ZONE", s, err,
            expected_status=409, expected_error="VEHICLE_OUTSIDE_ALLOWED_ZONE")

    # Step 4: аренда всё ещё STARTED
    s, rental2 = c.get_rental(RENTAL_ID)
    ch.step("GET /rentals — статус STARTED не изменился", s, rental2,
            expected_status=200, expected_fields={"status": "STARTED"})

    # Step 5: авто всё ещё IN_USE
    s, veh = c.get_vehicle(VEHICLE_ID)
    ch.step("GET /vehicles — авто осталось IN_USE", s, veh,
            expected_status=200, expected_fields={"status": "IN_USE"})

    # Step 6: RENTAL_FINISHED не должно быть в outbox
    outbox_finished = c.get_outbox("RENTAL_FINISHED")
    ch.check("outbox НЕ содержит RENTAL_FINISHED", len(outbox_finished) == 0)

    # Step 7: возвращаем авто в зону
    c.set_inside_zone(VEHICLE_ID)
    cfg2 = c.get_config()
    ch.check(f"{VEHICLE_ID} убран из vehicles_outside_zone",
             VEHICLE_ID not in cfg2.get("vehicles_outside_zone", []))

    # Step 8: теперь finish проходит
    s, finished = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish (авто в зоне) -> 200 FINISHED", s, finished,
            expected_status=200, expected_fields={"status": "FINISHED"})

    # ---- Вариация: блокировка из-за критического технического события ----
    c.reset()
    c.block_rental_finish(RENTAL_ID)

    s, err2 = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish (tech block) -> 409 RENTAL_FINISH_BLOCKED", s, err2,
            expected_status=409, expected_error="RENTAL_FINISH_BLOCKED")

    c.unblock_rental_finish(RENTAL_ID)
    s, fin2 = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish после снятия блокировки -> 200 FINISHED", s, fin2,
            expected_status=200, expected_fields={"status": "FINISHED"})

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
