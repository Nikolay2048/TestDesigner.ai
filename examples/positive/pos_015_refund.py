"""
POSITIVE — Case 015: Возврат средств после завершённой аренды

Вариации:
  A. Полный возврат -> платёж REFUNDED
  B. Частичный возврат -> платёж PARTIALLY_REFUNDED
  C. Повторный возврат после полного -> 409 PAYMENT_ALREADY_REFUNDED
  D. Возврат с суммой > amount -> 422
  E. Возврат по несуществующему платежу -> 404

FLK: FLK-PAYMENT-001 (нет дублирования), flk_payment_capturable
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("POS-015 | Возврат средств (full / partial)")
    c.reset()

    # ---- Вариант A: полный возврат ----
    PAYMENT_ID = "payment-captured"

    s, pay = c.get_payment(PAYMENT_ID)
    ch.step("GET /payments/payment-captured — CAPTURED, amount=240", s, pay,
            expected_status=200, expected_fields={"status": "CAPTURED"})
    original_amount = pay.get("amount", 240.0)

    s, refund = c.refund_payment(PAYMENT_ID, original_amount, reason="CUSTOMER_COMPLAINT")
    ch.step(f"POST /payments/refund — полный возврат {original_amount}₽ -> 201", s, refund,
            expected_status=201)
    ch.check("refundId получен", bool(refund.get("refundId")))
    ch.check("сумма возврата совпадает", refund.get("amount") == original_amount)
    ch.check("reason сохранён", refund.get("reason") == "CUSTOMER_COMPLAINT")

    s, pay_after = c.get_payment(PAYMENT_ID)
    ch.step("GET /payments — статус REFUNDED после полного возврата", s, pay_after,
            expected_status=200, expected_fields={"status": "REFUNDED"})

    # Повторный возврат после REFUNDED -> 409
    s, err = c.refund_payment(PAYMENT_ID, 100.0)
    ch.step("POST /payments/refund повторно -> 409 PAYMENT_ALREADY_REFUNDED", s, err,
            expected_status=409, expected_error="PAYMENT_ALREADY_REFUNDED")

    # ---- Вариант B: частичный возврат (нужен свежий платёж) ----
    c.reset()

    # Создадим новый захваченный платёж через полный флоу
    s, booking = c.create_booking("user-verified", "vehicle-available-4")
    bid = booking.get("bookingId")
    _, rental = c.start_rental(bid)
    rid = rental.get("rentalId")
    c.finish_rental(rid)
    s, pay2 = c.create_payment(rid)
    ch.step("Создан новый CAPTURED платёж (вариант B)", s, pay2,
            expected_status=201, expected_fields={"status": "CAPTURED"})
    pay2_id = pay2.get("paymentId")
    total = pay2.get("amount", 10.0)
    partial = round(total / 2, 2)

    s, partial_refund = c.refund_payment(pay2_id, partial, reason="PARTIAL_REFUND")
    ch.step(f"POST /payments/refund — частичный возврат {partial}₽ -> 201", s, partial_refund,
            expected_status=201)

    s, pay2_after = c.get_payment(pay2_id)
    ch.step("GET /payments — статус PARTIALLY_REFUNDED", s, pay2_after,
            expected_status=200, expected_fields={"status": "PARTIALLY_REFUNDED"})

    # Возврат суммы > amount -> 422
    s, err2 = c.refund_payment(pay2_id, total * 10)
    ch.step("POST /payments/refund amount > original -> 422 REFUND_AMOUNT_TOO_HIGH", s, err2,
            expected_status=422, expected_error="REFUND_AMOUNT_TOO_HIGH")

    # Возврат нулевой суммы -> 422
    s, err3 = c.refund_payment(pay2_id, 0)
    ch.step("POST /payments/refund amount=0 -> 422 REFUND_AMOUNT_INVALID", s, err3,
            expected_status=422, expected_error="REFUND_AMOUNT_INVALID")

    # Возврат по несуществующему платежу -> 404
    s, err4 = c.refund_payment("payment-nonexistent", 100.0)
    ch.step("POST /payments/refund несуществующий paymentId -> 404", s, err4,
            expected_status=404, expected_error="PAYMENT_NOT_FOUND")

    # outbox
    refund_events = c.get_outbox("PAYMENT_REFUNDED")
    ch.check("outbox содержит PAYMENT_REFUNDED", len(refund_events) > 0)

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
