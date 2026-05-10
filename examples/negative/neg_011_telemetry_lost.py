"""
NEGATIVE — Case 011: Потеря телеметрии во время активной аренды

Вариации:
  A. Телеметрия недоступна (signal lost) -> GET /telemetry возвращает stale=True, available=False
  B. Сервис телеметрии полностью недоступен -> 503
  C. При потере телеметрии finish принимается (двигатель выкл по умолчанию)
  D. При потере телеметрии, если engine_on=True — finish блокируется
  E. Восстановление телеметрии -> нормальный статус
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

RENTAL_ID = "rental-started"
VEHICLE_ID = "vehicle-in-use"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("NEG-011 | Потеря телеметрии во время аренды")
    c.reset()

    # Step 1: нормальная телеметрия
    s, telem = c.get_telemetry(VEHICLE_ID)
    ch.step("GET /telemetry — нормально, available=True, stale=False", s, telem,
            expected_status=200, expected_fields={"available": True})
    ch.check("stale=False изначально", not telem.get("stale", True))

    # ---- Вариант A: signal lost ----
    c.set_telemetry_lost(VEHICLE_ID)

    s, telem_lost = c.get_telemetry(VEHICLE_ID)
    ch.step("GET /telemetry (signal lost) -> 200, available=False, stale=True", s, telem_lost,
            expected_status=200,
            expected_fields={"available": False, "stale": True})
    ch.check("last_known содержит последние данные", "last_known" in telem_lost)

    # ---- Вариант B: сервис телеметрии полностью выключен ----
    c.disable_service("telemetry")

    s, telem_down = c.get_telemetry(VEHICLE_ID)
    ch.step("GET /telemetry (сервис выключен) -> 503 TELEMETRY_SERVICE_UNAVAILABLE", s, telem_down,
            expected_status=503, expected_error="TELEMETRY_SERVICE_UNAVAILABLE")

    c.enable_service("telemetry")

    # ---- Вариант C: при потере сигнала finish допустим (last known: engine_off) ----
    # Устанавливаем безопасное состояние телеметрии ДО потери
    c.update_telemetry(VEHICLE_ID, engine_on=False, doors_closed=True, speed_kmh=0)
    c.set_telemetry_lost(VEHICLE_ID)

    s, fin = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish при потере телеметрии (last known: engine_off) -> 200", s, fin,
            expected_status=200, expected_fields={"status": "FINISHED"})
    c.set_telemetry_restored(VEHICLE_ID)

    # ---- Вариант D: engine_on=True -> finish заблокирован ----
    c.reset()
    c.update_telemetry(VEHICLE_ID, engine_on=True, doors_closed=True, speed_kmh=0)

    s, err_engine = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish при engine_on=True -> 409 ENGINE_NOT_OFF", s, err_engine,
            expected_status=409, expected_error="ENGINE_NOT_OFF")

    # doors_closed=False -> тоже блокирует
    c.update_telemetry(VEHICLE_ID, engine_on=False, doors_closed=False, speed_kmh=0)
    s, err_doors = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish при doors_closed=False -> 409 DOORS_NOT_CLOSED", s, err_doors,
            expected_status=409, expected_error="DOORS_NOT_CLOSED")

    # speed_kmh > 0 -> тоже блокирует
    c.update_telemetry(VEHICLE_ID, engine_on=False, doors_closed=True, speed_kmh=15)
    s, err_speed = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish при speed_kmh=15 -> 409 VEHICLE_MOVING", s, err_speed,
            expected_status=409, expected_error="VEHICLE_MOVING")

    # ---- Вариант E: после приведения в норму finish проходит ----
    c.update_telemetry(VEHICLE_ID, engine_on=False, doors_closed=True, speed_kmh=0)
    s, fin2 = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish (все условия выполнены) -> 200 FINISHED", s, fin2,
            expected_status=200, expected_fields={"status": "FINISHED"})

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
