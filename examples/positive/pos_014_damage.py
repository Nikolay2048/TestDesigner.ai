"""
POSITIVE — Case 014: Damage-report после завершения аренды

Сценарий:
  1. POST /damage-reports — создание с severity=LOW -> 201
  2. POST /damage-reports — severity=HIGH + photoUrls -> 201
  3. GET /damage-reports/{id} -> 200
  4. Негатив: damage-report по активной аренде -> 409
  5. Негатив: отсутствует description -> 422
  6. Негатив: неверный severity -> 422
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from examples.client import CarshARingClient, ChainRunner

RENTAL_ID = "rental-finished"
VEHICLE_ID = "vehicle-available-5"
ACTIVE_RENTAL_ID = "rental-started"
ACTIVE_VEHICLE_ID = "vehicle-in-use"


def run(base_url: str = "http://127.0.0.1:8080") -> bool:
    c = CarshARingClient(base_url)
    ch = ChainRunner("POS-014 | Damage report после аренды")
    c.reset()

    # Step 1: аренда завершена
    s, rental = c.get_rental(RENTAL_ID)
    ch.step("GET /rentals/rental-finished — FINISHED", s, rental,
            expected_status=200, expected_fields={"status": "FINISHED"})

    # Step 2: лёгкое повреждение
    s, rep1 = c.create_damage_report(RENTAL_ID, VEHICLE_ID, "Царапина на переднем бампере", severity="LOW")
    ch.step("POST /damage-reports — LOW severity -> 201 CREATED", s, rep1,
            expected_status=201, expected_fields={"status": "CREATED", "severity": "LOW"})
    rep1_id = rep1.get("reportId", "")
    ch.check("reportId получен", bool(rep1_id))

    # Step 3: серьёзное повреждение с фото
    s, rep2 = c.create_damage_report(
        RENTAL_ID, VEHICLE_ID,
        "Глубокая вмятина на двери водителя, разбито зеркало",
        severity="HIGH",
        photo_urls=["https://cdn.example.com/photo1.jpg", "https://cdn.example.com/photo2.jpg"],
    )
    ch.step("POST /damage-reports — HIGH severity + 2 фото -> 201", s, rep2,
            expected_status=201, expected_fields={"severity": "HIGH"})
    ch.check("photoUrls сохранены (2 фото)", len(rep2.get("photoUrls", [])) == 2)

    # Step 4: GET отчёта
    s, check = c.get_damage_report(rep1_id)
    ch.step(f"GET /damage-reports/{rep1_id} -> 200", s, check,
            expected_status=200, expected_fields={"reportId": rep1_id})

    # Step 5: damage-report по активной аренде -> 409
    s, err = c.create_damage_report(ACTIVE_RENTAL_ID, ACTIVE_VEHICLE_ID, "Описание", severity="LOW")
    ch.step("POST /damage-reports по активной аренде -> 409 RENTAL_NOT_CLOSED", s, err,
            expected_status=409, expected_error="RENTAL_NOT_CLOSED")

    # Step 6: нет description -> 422
    s, err2 = c.post("/v1/damage-reports", {
        "rentalId": RENTAL_ID, "vehicleId": VEHICLE_ID, "severity": "LOW"
    })
    ch.step("POST /damage-reports без description -> 422", s, err2,
            expected_status=422, expected_error="DAMAGE_DESCRIPTION_REQUIRED")

    # Step 7: неверный severity -> 422
    s, err3 = c.create_damage_report(RENTAL_ID, VEHICLE_ID, "Test", severity="SEVERE")
    ch.step("POST /damage-reports severity=SEVERE (не из enum) -> 422", s, err3,
            expected_status=422, expected_error="DAMAGE_SEVERITY_INVALID")

    # Step 8: несуществующая аренда -> 404
    s, err4 = c.create_damage_report("rental-nonexistent", VEHICLE_ID, "Test")
    ch.step("POST /damage-reports — несуществующая аренда -> 404", s, err4,
            expected_status=404, expected_error="RENTAL_NOT_FOUND")

    # outbox
    dmg_events = c.get_outbox("DAMAGE_REPORT_CREATED")
    ch.check("outbox содержит 2 события DAMAGE_REPORT_CREATED", len(dmg_events) == 2)

    return ch.result()


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
