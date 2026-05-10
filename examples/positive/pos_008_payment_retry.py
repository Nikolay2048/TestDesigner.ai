"""
POSITIVE — Case 008: Повторная попытка оплаты после ошибки провайдера

Сценарий:
  1. Провайдер недоступен -> платёж FAILED
  2. Провайдер восстановлен -> POST /payments/{id}/retry -> CAPTURED
  3. Аренда переходит в PAID
  4. Проверка счётчика retry_count
  5. Проверка что следующий retry отклоняется (статус не FAILED)

FLK: FLK-PAYMENT-001 (нет дублирования), retry_limit_not_exceeded
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("POS-008 | Retry платежа: FAILED -> retry -> CAPTURED")
    c.reset()

    # Используем pre-seeded данные: rental-for-retry (PAYMENT_FAILED), payment-failed (FAILED)
    RENTAL_ID = "rental-for-retry"
    PAYMENT_ID = "payment-failed"

    # Step 1: убедиться что rental в PAYMENT_FAILED
    s, rental = c.get_rental(RENTAL_ID)
    ch.step("GET /rentals/rental-for-retry — PAYMENT_FAILED", s, rental,
            expected_status=200, expected_fields={"status": "PAYMENT_FAILED"})

    # Step 2: убедиться что платёж в FAILED
    s, pay = c.get_payment(PAYMENT_ID)
    ch.step("GET /payments/payment-failed — статус FAILED", s, pay,
            expected_status=200, expected_fields={"status": "FAILED"})
    ch.check("retry_count = 0 изначально", pay.get("retry_count", -1) == 0)

    # Step 3: retry при недоступном провайдере -> снова FAILED
    c.disable_service("payment")
    s, retry1 = c.retry_payment(PAYMENT_ID)
    ch.step("POST /payments/retry при недоступном провайдере -> FAILED", s, retry1,
            expected_status=200, expected_fields={"status": "FAILED"})
    ch.check("retry_count увеличился до 1", retry1.get("retry_count") == 1)
    c.enable_service("payment")

    # Step 4: retry при доступном провайдере -> CAPTURED
    s, retry2 = c.retry_payment(PAYMENT_ID)
    ch.step("POST /payments/retry при доступном провайдере -> CAPTURED", s, retry2,
            expected_status=200, expected_fields={"status": "CAPTURED"})
    ch.check("retry_count = 2", retry2.get("retry_count") == 2)

    # Step 5: аренда перешла в PAID
    s, rental_final = c.get_rental(RENTAL_ID)
    ch.step("GET /rentals — статус PAID после успешного retry", s, rental_final,
            expected_status=200, expected_fields={"status": "PAID"})

    # Step 6: ещё один retry отклоняется — статус уже не FAILED
    s, retry3 = c.retry_payment(PAYMENT_ID)
    ch.step("POST /payments/retry на CAPTURED -> 409 PAYMENT_NOT_FAILED", s, retry3,
            expected_status=409, expected_error="PAYMENT_NOT_FAILED")

    # Step 7: outbox содержит PAYMENT_CAPTURED
    outbox = c.get_outbox("PAYMENT_CAPTURED")
    ch.check("outbox содержит PAYMENT_CAPTURED", len(outbox) > 0)

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
