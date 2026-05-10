"""
POSITIVE — Case 010: Автоматическое истечение бронирования системой

Сценарий:
  1. Бронирование с истёкшим TTL -> CONFIRMED но expires_at в прошлом
  2. POST /bookings/{id}/expire (инициатор — планировщик) -> 200 EXPIRED
  3. Автомобиль освобождается -> AVAILABLE
  4. Попытка старта аренды по истёкшему бронированию -> 409
  5. Вариация: expire CONFIRMED-бронирования с действующим TTL (тоже работает)
  6. Проверка outbox — BOOKING_EXPIRED

FLK: бронирование должно быть в CONFIRMED/CREATED для expire.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

# Pre-seeded: booking-to-expire — CONFIRMED, expires_at в прошлом
BOOKING_EXPIRED_TTL = "booking-to-expire"
VEHICLE_EXPIRED_TTL = "vehicle-available-3"

# Pre-seeded: booking-confirmed — нормальное бронирование (TTL не истёк)
BOOKING_NORMAL = "booking-confirmed"
VEHICLE_NORMAL = "vehicle-available-1"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("POS-010 | Авто-истечение бронирования (планировщик)")
    c.reset()

    # ---- Вариант А: истёкший TTL ----
    s, bk = c.get_booking(BOOKING_EXPIRED_TTL)
    ch.step("GET /bookings/booking-to-expire — CONFIRMED (TTL в прошлом)", s, bk,
            expected_status=200, expected_fields={"status": "CONFIRMED"})
    ch.check("expires_at существует", bool(bk.get("expires_at")))

    s, veh = c.get_vehicle(VEHICLE_EXPIRED_TTL)
    ch.step(f"GET /vehicles/{VEHICLE_EXPIRED_TTL} — RESERVED до expire", s, veh,
            expected_status=200, expected_fields={"status": "RESERVED"})

    s, expired = c.expire_booking(BOOKING_EXPIRED_TTL)
    ch.step("POST /bookings/expire (планировщик) -> 200 EXPIRED", s, expired,
            expected_status=200, expected_fields={"status": "EXPIRED"})

    s, veh2 = c.get_vehicle(VEHICLE_EXPIRED_TTL)
    ch.step(f"GET /vehicles/{VEHICLE_EXPIRED_TTL} — вернулся в AVAILABLE", s, veh2,
            expected_status=200, expected_fields={"status": "AVAILABLE"})

    # Попытка старта аренды по истёкшему бронированию
    s, start_err = c.start_rental(BOOKING_EXPIRED_TTL)
    ch.step("POST /rentals/start по EXPIRED бронированию -> 409", s, start_err,
            expected_status=409, expected_error="BOOKING_STATUS_INVALID")

    # Повторный expire -> 409
    s, exp2 = c.expire_booking(BOOKING_EXPIRED_TTL)
    ch.step("POST /bookings/expire повторно -> 409 BOOKING_STATUS_INVALID", s, exp2,
            expected_status=409, expected_error="BOOKING_STATUS_INVALID")

    # ---- Вариант Б: нормальное бронирование (принудительный expire) ----
    s, bk2 = c.get_booking(BOOKING_NORMAL)
    ch.step("GET /bookings/booking-confirmed — CONFIRMED (действующий TTL)", s, bk2,
            expected_status=200, expected_fields={"status": "CONFIRMED"})

    s, exp_normal = c.expire_booking(BOOKING_NORMAL)
    ch.step("POST /bookings/expire нормального бронирования (принудительно) -> 200 EXPIRED", s, exp_normal,
            expected_status=200, expected_fields={"status": "EXPIRED"})

    s, veh3 = c.get_vehicle(VEHICLE_NORMAL)
    ch.step(f"GET /vehicles/{VEHICLE_NORMAL} — AVAILABLE после принудительного expire", s, veh3,
            expected_status=200, expected_fields={"status": "AVAILABLE"})

    # outbox
    outbox_types = {e["event_type"] for e in c.get_outbox()}
    ch.check("outbox содержит BOOKING_EXPIRED", "BOOKING_EXPIRED" in outbox_types)

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
