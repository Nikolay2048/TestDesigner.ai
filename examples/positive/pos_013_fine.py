"""
POSITIVE — Case 013: Создание штрафа после завершения аренды

Сценарий:
  1. Аренда завершена (FINISHED)
  2. POST /fines -> 201 CREATED
  3. GET /fines/{id} -> 200
  4. Несколько штрафов по одной аренде — допустимо
  5. Штраф с разными типами нарушений
  6. Негативная вариация: штраф по активной аренде -> 409
  7. Негативная вариация: штраф без обязательных полей -> 422
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

RENTAL_ID = "rental-finished"
USER_ID = "user-for-fine"
ACTIVE_RENTAL_ID = "rental-started"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("POS-013 | Штраф после завершения аренды")
    c.reset()

    # Step 1: аренда завершена
    s, rental = c.get_rental(RENTAL_ID)
    ch.step("GET /rentals/rental-finished — статус FINISHED", s, rental,
            expected_status=200, expected_fields={"status": "FINISHED"})

    # Step 2: штраф за нарушение парковки
    s, fine1 = c.create_fine(RENTAL_ID, USER_ID, 500.0, "PARKING_VIOLATION")
    ch.step("POST /fines — нарушение парковки, 500₽ -> 201 CREATED", s, fine1,
            expected_status=201, expected_fields={"status": "CREATED"})
    fine1_id = fine1.get("fineId", "")
    ch.check("fineId получен", bool(fine1_id))
    ch.check("amount = 500", fine1.get("amount") == 500.0)
    ch.check("rentalId привязан", fine1.get("rentalId") == RENTAL_ID)

    # Step 3: GET штрафа
    s, fine_check = c.get_fine(fine1_id)
    ch.step(f"GET /fines/{fine1_id} -> 200", s, fine_check,
            expected_status=200, expected_fields={"fineId": fine1_id})

    # Step 4: второй штраф по той же аренде (повреждение зоны)
    s, fine2 = c.create_fine(RENTAL_ID, USER_ID, 1000.0, "ZONE_DAMAGE")
    ch.step("POST /fines — зональный штраф, 1000₽ -> 201 (2 штрафа по одной аренде допустимо)", s, fine2,
            expected_status=201, expected_fields={"status": "CREATED"})

    # Step 5: штраф за превышение скорости (минимальная сумма)
    s, fine3 = c.create_fine(RENTAL_ID, USER_ID, 100.0, "SPEEDING")
    ch.step("POST /fines — превышение скорости, 100₽ -> 201", s, fine3,
            expected_status=201)

    # Step 6: штраф по активной аренде -> 409
    s, err_active = c.create_fine(ACTIVE_RENTAL_ID, "user-with-rental", 500.0, "PARKING_VIOLATION")
    ch.step("POST /fines по активной аренде -> 409 RENTAL_NOT_CLOSED", s, err_active,
            expected_status=409, expected_error="RENTAL_NOT_CLOSED")

    # Step 7: штраф без rentalId -> 422
    s, err_no_rental = c.post("/v1/fines", {"userId": USER_ID, "amount": 500, "reason": "SPEEDING"})
    ch.step("POST /fines без rentalId -> 422", s, err_no_rental,
            expected_status=422, expected_error="FINE_RENTAL_REQUIRED")

    # Step 8: штраф без суммы -> 422
    s, err_no_amount = c.post("/v1/fines", {"rentalId": RENTAL_ID, "userId": USER_ID, "reason": "SPEEDING"})
    ch.step("POST /fines без amount -> 422", s, err_no_amount,
            expected_status=422, expected_error="FINE_AMOUNT_INVALID")

    # Step 9: штраф с amount=0 -> 422
    s, err_zero = c.create_fine(RENTAL_ID, USER_ID, 0, "SPEEDING")
    ch.step("POST /fines amount=0 -> 422 FINE_AMOUNT_INVALID", s, err_zero,
            expected_status=422, expected_error="FINE_AMOUNT_INVALID")

    # outbox
    fine_events = c.get_outbox("FINE_CREATED")
    ch.check("outbox содержит 3 события FINE_CREATED", len(fine_events) == 3)

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
