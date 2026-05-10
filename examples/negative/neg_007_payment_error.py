"""
NEGATIVE — Case 007: Ошибка платёжного провайдера при списании

Вариации:
  A. Провайдер недоступен при создании платежа -> статус FAILED
  B. Аренда переходит в PAYMENT_FAILED
  C. В outbox нет PAYMENT_CAPTURED, есть PAYMENT_FAILED
  D. Созданный платёж можно повторить после восстановления провайдера
  E. Повторное создание платежа по той же аренде -> 409 (после успеха)

FLK-PAYMENT-001: no duplicate payment
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("NEG-007 | Ошибка платёжного провайдера")
    c.reset()

    # Подготовка: создать и завершить аренду
    s, booking = c.create_booking("user-verified", "vehicle-available-4")
    bid = booking.get("bookingId")
    s, rental = c.start_rental(bid)
    rid = rental.get("rentalId")
    s, finished = c.finish_rental(rid)
    ch.step("Аренда завершена (setup)", s, finished,
            expected_status=200, expected_fields={"status": "FINISHED"})
    amount = finished.get("amount", 10.0)

    # ---- Вариант A: провайдер недоступен ----
    c.disable_service("payment")
    cfg = c.get_config()
    ch.check("payment_provider_available = False", not cfg.get("payment_provider_available"))

    s, pay = c.create_payment(rid, amount)
    ch.step("POST /payments (провайдер недоступен) -> 201, status=FAILED", s, pay,
            expected_status=201, expected_fields={"status": "FAILED"})
    ch.check("failure_reason содержит PROVIDER", "PROVIDER" in (pay.get("failure_reason") or ""))
    pay_id = pay.get("paymentId")

    # ---- Вариант B: аренда в PAYMENT_FAILED ----
    s, rental_state = c.get_rental(rid)
    ch.step("GET /rentals — статус PAYMENT_FAILED", s, rental_state,
            expected_status=200, expected_fields={"status": "PAYMENT_FAILED"})

    # ---- Вариант C: outbox ----
    captured = c.get_outbox("PAYMENT_CAPTURED")
    failed = c.get_outbox("PAYMENT_FAILED")
    ch.check("outbox НЕ содержит PAYMENT_CAPTURED", len(captured) == 0)
    ch.check("outbox содержит PAYMENT_FAILED", len(failed) > 0)

    # ---- Вариант D: восстанавливаем провайдер и делаем retry ----
    c.enable_service("payment")
    cfg2 = c.get_config()
    ch.check("payment_provider_available = True", cfg2.get("payment_provider_available"))

    s, retry = c.retry_payment(pay_id)
    ch.step("POST /payments/retry (провайдер восстановлен) -> 200 CAPTURED", s, retry,
            expected_status=200, expected_fields={"status": "CAPTURED"})

    s, rental_paid = c.get_rental(rid)
    ch.step("GET /rentals — статус PAID после успешного retry", s, rental_paid,
            expected_status=200, expected_fields={"status": "PAID"})

    # ---- Вариант E: попытка создать второй платёж после успеха -> 409 ----
    s, err = c.create_payment(rid, amount)
    ch.step("POST /payments повторно (уже CAPTURED) -> 409 PAYMENT_OPERATION_DUPLICATE", s, err,
            expected_status=409, expected_error="PAYMENT_OPERATION_DUPLICATE")

    # ---- Вариант F: попытка создать платёж по несуществующей аренде -> 404 ----
    s, err2 = c.create_payment("rental-nonexistent", 100.0)
    ch.step("POST /payments rental-nonexistent -> 404 RENTAL_NOT_FOUND", s, err2,
            expected_status=404, expected_error="RENTAL_NOT_FOUND")

    # ---- Вариант G: попытка платежа по STARTED аренде -> 409 ----
    s, err3 = c.create_payment("rental-started", 100.0)
    ch.step("POST /payments по STARTED аренде -> 409 RENTAL_NOT_FINISHED", s, err3,
            expected_status=409, expected_error="RENTAL_NOT_FINISHED")

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
