"""
NEGATIVE — Case 012: Повторный запрос на завершение аренды (идемпотентность)

Сценарий:
  1. Завершаем аренду -> 200 FINISHED
  2. Повторный finish того же rental -> 409 RENTAL_STATUS_INVALID
  3. Статус и данные аренды не изменились после отказа
  4. Вариация: повторный старт уже запущенной аренды
  5. Вариация: попытка старта по bookingId, уже используемому
  6. Вариация: повторное создание платежа (дублирование)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

RENTAL_ID = "rental-started"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("NEG-012 | Идемпотентность: повторный finish / start")
    c.reset()

    # Step 1: первый finish -> успех
    s, fin1 = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish (первый раз) -> 200 FINISHED", s, fin1,
            expected_status=200, expected_fields={"status": "FINISHED"})
    amount_first = fin1.get("amount")
    finished_at_first = fin1.get("finished_at")

    # Step 2: второй finish -> 409
    s, fin2 = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish (повторно) -> 409 RENTAL_STATUS_INVALID", s, fin2,
            expected_status=409, expected_error="RENTAL_STATUS_INVALID")

    # Step 3: данные аренды не изменились
    s, rent = c.get_rental(RENTAL_ID)
    ch.step("GET /rentals — статус всё ещё FINISHED (не изменился)", s, rent,
            expected_status=200, expected_fields={"status": "FINISHED"})
    ch.check("amount не изменился", rent.get("amount") == amount_first)
    ch.check("finished_at не изменился", rent.get("finished_at") == finished_at_first)

    # Step 4: третий finish -> снова 409 (устойчивость)
    s, fin3 = c.finish_rental(RENTAL_ID)
    ch.step("POST /rentals/finish (третий раз) -> 409 (устойчивость)", s, fin3,
            expected_status=409, expected_error="RENTAL_STATUS_INVALID")

    # Step 5: RENTAL_FINISHED в outbox только один раз
    finished_events = c.get_outbox("RENTAL_FINISHED")
    ch.check("outbox содержит ровно 1 событие RENTAL_FINISHED", len(finished_events) == 1)

    # ---- Вариация: попытка дублирования start ----
    c.reset()

    # booking-started уже в STARTED, связан с rental-started
    s, err_start = c.start_rental("booking-started")
    ch.step("POST /rentals/start по booking-started (уже STARTED) -> 409", s, err_start,
            expected_status=409, expected_error="BOOKING_STATUS_INVALID")

    # ---- Вариация: пользователь с активной арендой пытается начать вторую ----
    c.reset()

    # user-with-rental уже имеет rental-started
    s, bk = c.create_booking("user-with-rental", "vehicle-available-1")
    ch.step("POST /bookings для user-with-rental -> 409 USER_HAS_ACTIVE_RENTAL", s, bk,
            expected_status=409, expected_error="USER_HAS_ACTIVE_RENTAL")

    # ---- Вариация: дублирование платежа ----
    c.reset()

    # rental-for-refund уже имеет CAPTURED платёж
    s, pay_dup = c.create_payment("rental-for-refund", 240.0)
    ch.step("POST /payments повторно по rental-for-refund -> 409 PAYMENT_OPERATION_DUPLICATE", s, pay_dup,
            expected_status=409, expected_error="PAYMENT_OPERATION_DUPLICATE")

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
