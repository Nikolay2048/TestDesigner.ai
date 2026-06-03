"""
Requests-based chain tests for the synthetic car-rental mock.

Purpose:
1. Verify the mock server supports all intended end-to-end chains.
2. Keep a canonical call sequence that can be compared with an external executor trace.

Run against a started server:
    BASE_URL=http://localhost:8080 pytest -q tests

Optional executor trace validation:
    EXECUTOR_TRACE_FILE=executor_trace.json pytest -q tests/test_chains_requests.py::test_executor_trace_contains_required_chains

Supported trace formats:
- [{"method": "GET", "path": "/locations", "status_code": 200}, ...]
- {"calls": [{"method": "POST", "url": "http://host/reservations", "status": 201}, ...]}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import pytest
import requests

BASE_URL = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "3"))
TRACE_OUT = os.getenv("CHAIN_TRACE_OUT")

CALLS: list[dict[str, Any]] = []

EXPECTED_CHAINS: dict[str, list[tuple[str, str]]] = {
    "01_basic_economy_rental": [
        ("GET", "/locations"),
        ("POST", "/vehicles/search"),
        ("GET", "/vehicles/{vehicleId}"),
        ("POST", "/reservations"),
        ("GET", "/reservations/{reservationId}"),
        ("POST", "/payments/preauth"),
        ("POST", "/rentals/{reservationId}/pickup"),
        ("GET", "/rentals/{rentalId}"),
        ("POST", "/rentals/{rentalId}/return"),
    ],
    "02_loyalty_extras_rental": [
        ("GET", "/locations"),
        ("POST", "/vehicles/search"),
        ("POST", "/reservations"),
        ("POST", "/reservations/{reservationId}/extras"),
        ("POST", "/loyalty/validate"),
        ("GET", "/reservations/{reservationId}"),
        ("POST", "/payments/preauth"),
        ("POST", "/rentals/{reservationId}/pickup"),
    ],
    "03_one_way_suv_airport": [
        ("GET", "/locations"),
        ("POST", "/vehicles/search"),
        ("GET", "/vehicles/{vehicleId}"),
        ("POST", "/reservations"),
        ("GET", "/reservations/{reservationId}"),
        ("POST", "/payments/preauth"),
        ("POST", "/rentals/{reservationId}/pickup"),
        ("GET", "/rentals/{rentalId}"),
    ],
    "04_extend_rental": [
        ("GET", "/locations"),
        ("POST", "/vehicles/search"),
        ("POST", "/reservations"),
        ("POST", "/payments/preauth"),
        ("POST", "/rentals/{reservationId}/pickup"),
        ("GET", "/rentals/{rentalId}"),
        ("POST", "/rentals/{rentalId}/extend"),
        ("GET", "/rentals/{rentalId}"),
    ],
    "05_return_with_incident": [
        ("GET", "/locations"),
        ("POST", "/vehicles/search"),
        ("POST", "/reservations"),
        ("POST", "/payments/preauth"),
        ("POST", "/rentals/{reservationId}/pickup"),
        ("POST", "/incidents"),
        ("POST", "/rentals/{rentalId}/return"),
    ],
}


def record(method: str, path: str, response: requests.Response) -> None:
    CALLS.append(
        {
            "method": method.upper(),
            "path": path,
            "status_code": response.status_code,
            "ok": response.ok,
        }
    )


def request(method: str, path: str, *, expected: int | Iterable[int] = 200, **kwargs: Any) -> requests.Response:
    response = requests.request(method, BASE_URL + path, timeout=TIMEOUT, **kwargs)
    record(method, path, response)
    allowed = {expected} if isinstance(expected, int) else set(expected)
    assert response.status_code in allowed, response.text
    return response


def get_json(method: str, path: str, *, expected: int | Iterable[int] = 200, **kwargs: Any) -> dict[str, Any]:
    return request(method, path, expected=expected, **kwargs).json()


@pytest.fixture(scope="session", autouse=True)
def server_is_alive() -> None:
    try:
        response = requests.get(BASE_URL + "/locations", timeout=TIMEOUT)
    except requests.RequestException as exc:
        pytest.fail(f"Mock server is not reachable at {BASE_URL}: {exc}")
    assert response.status_code == 200, response.text


def locations() -> tuple[str, str]:
    data = get_json("GET", "/locations")
    assert len(data["locations"]) >= 2
    city = next(item["id"] for item in data["locations"] if item["airport"] is False)
    airport = next(item["id"] for item in data["locations"] if item["airport"] is True)
    return city, airport


def search_vehicle(category: str, driver_age: int, pickup: str, ret: str, pickup_location: str, return_location: str) -> dict[str, Any]:
    data = get_json(
        "POST",
        "/vehicles/search",
        json={
            "pickupLocationId": pickup_location,
            "returnLocationId": return_location,
            "pickupDate": pickup,
            "returnDate": ret,
            "driverAge": driver_age,
            "category": category,
        },
    )
    assert data["vehicles"], data
    vehicle = data["vehicles"][0]
    assert vehicle["category"] == category
    return vehicle


def create_reservation(vehicle_id: str, pickup_location: str, return_location: str, pickup: str, ret: str) -> dict[str, Any]:
    return get_json(
        "POST",
        "/reservations",
        expected=201,
        json={
            "vehicleId": vehicle_id,
            "pickupLocationId": pickup_location,
            "returnLocationId": return_location,
            "pickupDate": pickup,
            "returnDate": ret,
            "customer": {
                "firstName": "Ivan",
                "lastName": "Petrov",
                "phone": "+79990000000",
                "email": "ivan.petrov@example.com",
                "driverLicenseNo": "DL-123456",
            },
        },
    )


def authorize_current_amount(reservation_id: str) -> dict[str, Any]:
    reservation = get_json("GET", f"/reservations/{reservation_id}")
    payment = get_json(
        "POST",
        "/payments/preauth",
        json={
            "reservationId": reservation_id,
            "cardToken": "tok_approved",
            "amount": reservation["totalAmount"],
        },
    )
    assert payment["status"] == "AUTHORIZED"
    return payment


def pickup_rental(reservation_id: str, odometer: int = 10_000) -> str:
    data = get_json(
        "POST",
        f"/rentals/{reservation_id}/pickup",
        json={"odometer": odometer, "fuelLevelPercent": 100, "damageConfirmed": True},
    )
    assert data["status"] == "ACTIVE"
    return data["rentalId"]


def create_paid_active_rental(category: str = "ECONOMY", driver_age: int = 31, pickup: str = "2026-06-10", ret: str = "2026-06-12", one_way: bool = False) -> tuple[dict[str, Any], str]:
    city, airport = locations()
    return_location = airport if one_way else city
    vehicle = search_vehicle(category, driver_age, pickup, ret, city, return_location)
    reservation = create_reservation(vehicle["id"], city, return_location, pickup, ret)
    authorize_current_amount(reservation["id"])
    rental_id = pickup_rental(reservation["id"])
    return reservation, rental_id


def test_01_basic_economy_rental_chain() -> None:
    city, _ = locations()
    vehicle = search_vehicle("ECONOMY", 31, "2026-06-10", "2026-06-12", city, city)
    vehicle_details = get_json("GET", f"/vehicles/{vehicle['id']}")
    assert vehicle_details["deposit"] == vehicle["deposit"]

    reservation = create_reservation(vehicle["id"], city, city, "2026-06-10", "2026-06-12")
    assert reservation["status"] == "DRAFT"

    authorize_current_amount(reservation["id"])
    confirmed = get_json("GET", f"/reservations/{reservation['id']}")
    assert confirmed["status"] == "CONFIRMED"

    rental_id = pickup_rental(reservation["id"])
    rental = get_json("GET", f"/rentals/{rental_id}")
    assert rental["status"] == "ACTIVE"

    result = get_json("POST", f"/rentals/{rental_id}/return", json={"odometer": 10_120, "fuelLevelPercent": 100})
    assert result == {"status": "CLOSED", "extraCharge": 0.0}


def test_02_loyalty_extras_chain_with_stabilization_hints() -> None:
    city, _ = locations()
    vehicle = search_vehicle("ECONOMY", 31, "2026-06-15", "2026-06-17", city, city)
    reservation = create_reservation(vehicle["id"], city, city, "2026-06-15", "2026-06-17")

    with_extras = get_json("POST", f"/reservations/{reservation['id']}/extras", json={"extras": ["CHILD_SEAT", "GPS"]})
    assert set(with_extras["extras"]) == {"CHILD_SEAT", "GPS"}

    bad_loyalty = get_json(
        "POST",
        "/loyalty/validate",
        expected=400,
        json={"reservationId": reservation["id"], "loyaltyNumber": "LC-100200"},
    )
    assert bad_loyalty["detail"]["code"] == "LOYALTY_NOT_FOUND"
    assert "LOYAL-GOLD-777" in bad_loyalty["detail"]["hint"]

    ok_loyalty = get_json(
        "POST",
        "/loyalty/validate",
        json={"reservationId": reservation["id"], "loyaltyNumber": "LOYAL-GOLD-777"},
    )
    assert ok_loyalty["valid"] is True

    after_discount = get_json("GET", f"/reservations/{reservation['id']}")
    assert after_discount["loyaltyDiscountApplied"] is True
    authorize_current_amount(reservation["id"])
    pickup_rental(reservation["id"])


def test_03_one_way_suv_airport_chain_requires_driver_30_plus() -> None:
    city, airport = locations()
    too_young = get_json(
        "POST",
        "/vehicles/search",
        expected=400,
        json={
            "pickupLocationId": city,
            "returnLocationId": airport,
            "pickupDate": "2026-06-20",
            "returnDate": "2026-06-23",
            "driverAge": 29,
            "category": "SUV",
        },
    )
    assert too_young["detail"]["code"] == "DRIVER_TOO_YOUNG_FOR_SUV"
    assert "driverAge=30" in too_young["detail"]["hint"]

    vehicle = search_vehicle("SUV", 30, "2026-06-20", "2026-06-23", city, airport)
    details = get_json("GET", f"/vehicles/{vehicle['id']}")
    assert details["category"] == "SUV"

    reservation = create_reservation(vehicle["id"], city, airport, "2026-06-20", "2026-06-23")
    assert reservation["totalAmount"] > details["dailyRate"] * 3  # one-way fee is included
    authorize_current_amount(reservation["id"])
    rental_id = pickup_rental(reservation["id"])
    assert get_json("GET", f"/rentals/{rental_id}")["status"] == "ACTIVE"


def test_04_extend_rental_chain_uses_hint_after_invalid_extension() -> None:
    _, rental_id = create_paid_active_rental(pickup="2026-07-01", ret="2026-07-03")
    before = get_json("GET", f"/rentals/{rental_id}")
    assert before["returnDate"] == "2026-07-03"

    invalid = get_json("POST", f"/rentals/{rental_id}/extend", expected=409, json={"newReturnDate": "2026-07-03"})
    assert invalid["detail"]["code"] == "INVALID_EXTENSION_DATE"
    assert "2026-07-04" in invalid["detail"]["hint"]

    extended = get_json("POST", f"/rentals/{rental_id}/extend", json={"newReturnDate": "2026-07-04"})
    assert extended["newReturnDate"] == "2026-07-04"
    after = get_json("GET", f"/rentals/{rental_id}")
    assert after["returnDate"] == "2026-07-04"


def test_05_return_with_incident_chain_registers_incident_before_close() -> None:
    _, rental_id = create_paid_active_rental(pickup="2026-07-10", ret="2026-07-12")
    incident = get_json(
        "POST",
        "/incidents",
        expected=201,
        json={"rentalId": rental_id, "type": "SCRATCH", "description": "Scratch on rear bumper"},
    )
    assert incident["status"] == "OPEN"

    result = get_json("POST", f"/rentals/{rental_id}/return", json={"odometer": 10_250, "fuelLevelPercent": 100})
    assert result["status"] == "NEEDS_INSPECTION"

    late_incident = get_json(
        "POST",
        "/incidents",
        expected=400,
        json={"rentalId": rental_id, "type": "SCRATCH", "description": "Should be rejected after close"},
    )
    assert late_incident["detail"]["code"] == "RENTAL_ALREADY_CLOSED"


def normalize_trace_call(raw: dict[str, Any]) -> tuple[str, str]:
    method = str(raw.get("method", "")).upper()
    path = raw.get("path") or raw.get("url") or raw.get("endpoint") or ""
    if path.startswith("http://") or path.startswith("https://"):
        path = urlparse(path).path
    path = str(path)
    return method, path


def pattern_matches(pattern: str, actual: str) -> bool:
    pp = pattern.strip("/").split("/")
    aa = actual.strip("/").split("/")
    if len(pp) != len(aa):
        return False
    for p, a in zip(pp, aa):
        if p.startswith("{") and p.endswith("}"):
            continue
        if p != a:
            return False
    return True


def contains_subsequence(actual: list[tuple[str, str]], expected: list[tuple[str, str]]) -> bool:
    cursor = 0
    for method, path in actual:
        exp_method, exp_pattern = expected[cursor]
        if method == exp_method and pattern_matches(exp_pattern, path):
            cursor += 1
            if cursor == len(expected):
                return True
    return False


def test_expected_chain_contract_is_defined_for_all_scenarios() -> None:
    assert set(EXPECTED_CHAINS) == {
        "01_basic_economy_rental",
        "02_loyalty_extras_rental",
        "03_one_way_suv_airport",
        "04_extend_rental",
        "05_return_with_incident",
    }
    for steps in EXPECTED_CHAINS.values():
        assert steps[0] == ("GET", "/locations")
        assert any(step[0] == "GET" for step in steps)
        assert any(step[0] == "POST" for step in steps)


def test_executor_trace_contains_required_chains() -> None:
    trace_file = os.getenv("EXECUTOR_TRACE_FILE")
    if not trace_file:
        pytest.skip("Set EXECUTOR_TRACE_FILE to validate an external executor call trace")

    raw = json.loads(Path(trace_file).read_text(encoding="utf-8"))
    calls = raw.get("calls", raw) if isinstance(raw, dict) else raw
    actual = [normalize_trace_call(item) for item in calls]

    missing = [name for name, expected in EXPECTED_CHAINS.items() if not contains_subsequence(actual, expected)]
    assert not missing, f"Executor trace does not contain required chain subsequences: {missing}"


@pytest.fixture(scope="session", autouse=True)
def write_chain_trace_at_end(request: pytest.FixtureRequest) -> None:
    def fin() -> None:
        if TRACE_OUT:
            Path(TRACE_OUT).write_text(json.dumps({"calls": CALLS}, ensure_ascii=False, indent=2), encoding="utf-8")

    request.addfinalizer(fin)
