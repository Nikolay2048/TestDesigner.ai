"""
HTTP-клиент для работы с Carsharing Mock Server.
Использует только стандартную библиотеку (urllib).
"""

import json
import urllib.request
import urllib.error
from typing import Any


class APIError(Exception):
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body.get('error', '?')} — {body.get('message', '')}")


class CarshARingClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080"):
        self.base = base_url.rstrip("/")
        self._correlation_prefix = ""

    # ------------------------------------------------------------------ #
    #  Transport                                                           #
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, body: Any = None,
                 raise_on_error: bool = False) -> tuple[int, dict]:
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            payload = {}
            try:
                payload = json.loads(e.read())
            except Exception:
                pass
            if raise_on_error:
                raise APIError(e.code, payload)
            return e.code, payload

    def get(self, path: str) -> tuple[int, dict]:
        return self._request("GET", path)

    def post(self, path: str, body: Any = None) -> tuple[int, dict]:
        return self._request("POST", path, body)

    def put(self, path: str, body: Any = None) -> tuple[int, dict]:
        return self._request("PUT", path, body)

    # ------------------------------------------------------------------ #
    #  Mock controls                                                       #
    # ------------------------------------------------------------------ #

    def reset(self):
        self.post("/mock/reset")

    def seed_ids(self) -> dict:
        _, body = self.get("/mock/seed-ids")
        return body

    def get_state(self, collection: str = "") -> dict:
        path = f"/mock/state?collection={collection}" if collection else "/mock/state"
        _, body = self.get(path)
        return body

    def get_config(self) -> dict:
        _, body = self.get("/mock/config")
        return body

    def set_config(self, **kwargs) -> dict:
        _, body = self.post("/mock/config", kwargs)
        return body

    def get_outbox(self, event_type: str = "") -> list:
        path = f"/mock/outbox?type={event_type}" if event_type else "/mock/outbox"
        _, body = self.get(path)
        return body.get("events", [])

    def get_audit(self, entity_type: str = "", entity_id: str = "") -> list:
        params = []
        if entity_type:
            params.append(f"entity_type={entity_type}")
        if entity_id:
            params.append(f"entity_id={entity_id}")
        path = "/mock/audit" + ("?" + "&".join(params) if params else "")
        _, body = self.get(path)
        return body.get("logs", [])

    def set_outside_zone(self, vehicle_id: str):
        self.post(f"/mock/vehicles/{vehicle_id}/set-outside-zone")

    def set_inside_zone(self, vehicle_id: str):
        self.post(f"/mock/vehicles/{vehicle_id}/set-inside-zone")

    def disable_service(self, service: str):
        self.post(f"/mock/services/{service}/disable")

    def enable_service(self, service: str):
        self.post(f"/mock/services/{service}/enable")

    def set_telemetry_lost(self, vehicle_id: str):
        self.post(f"/mock/telemetry/{vehicle_id}/set-lost")

    def set_telemetry_restored(self, vehicle_id: str):
        self.post(f"/mock/telemetry/{vehicle_id}/set-restored")

    def block_rental_finish(self, rental_id: str):
        self.post(f"/mock/rentals/{rental_id}/block-finish")

    def unblock_rental_finish(self, rental_id: str):
        self.post(f"/mock/rentals/{rental_id}/unblock-finish")

    # ------------------------------------------------------------------ #
    #  Business API                                                        #
    # ------------------------------------------------------------------ #

    def get_user(self, user_id: str):
        return self.get(f"/v1/users/{user_id}")

    def get_driver_license(self, user_id: str):
        return self.get(f"/v1/users/{user_id}/driver-license")

    def get_vehicles_available(self):
        return self.get("/v1/vehicles/available")

    def get_vehicle(self, vehicle_id: str):
        return self.get(f"/v1/vehicles/{vehicle_id}")

    def get_telemetry(self, vehicle_id: str):
        return self.get(f"/v1/vehicles/{vehicle_id}/telemetry")

    def update_telemetry(self, vehicle_id: str, **fields):
        return self.put(f"/v1/vehicles/{vehicle_id}/telemetry", fields)

    def create_booking(self, user_id: str, vehicle_id: str):
        return self.post("/v1/bookings", {"userId": user_id, "vehicleId": vehicle_id})

    def get_booking(self, booking_id: str):
        return self.get(f"/v1/bookings/{booking_id}")

    def cancel_booking(self, booking_id: str, reason: str = "USER_REQUEST"):
        return self.post(f"/v1/bookings/{booking_id}/cancel", {"reason": reason})

    def expire_booking(self, booking_id: str):
        return self.post(f"/v1/bookings/{booking_id}/expire")

    def start_rental(self, booking_id: str, user_location: dict = None):
        body = {"bookingId": booking_id}
        if user_location:
            body["userLocation"] = user_location
        return self.post("/v1/rentals/start", body)

    def get_rental(self, rental_id: str):
        return self.get(f"/v1/rentals/{rental_id}")

    def finish_rental(self, rental_id: str):
        return self.post(f"/v1/rentals/{rental_id}/finish")

    def create_payment(self, rental_id: str, amount: float = None, currency: str = "RUB"):
        body = {"rentalId": rental_id, "currency": currency}
        if amount is not None:
            body["amount"] = amount
        return self.post("/v1/payments", body)

    def get_payment(self, payment_id: str):
        return self.get(f"/v1/payments/{payment_id}")

    def retry_payment(self, payment_id: str):
        return self.post(f"/v1/payments/{payment_id}/retry")

    def refund_payment(self, payment_id: str, amount: float, reason: str = "CUSTOMER_REQUEST"):
        return self.post(f"/v1/payments/{payment_id}/refund", {"amount": amount, "reason": reason})

    def create_fine(self, rental_id: str, user_id: str, amount: float, reason: str):
        return self.post("/v1/fines", {
            "rentalId": rental_id, "userId": user_id,
            "amount": amount, "reason": reason,
        })

    def get_fine(self, fine_id: str):
        return self.get(f"/v1/fines/{fine_id}")

    def create_damage_report(self, rental_id: str, vehicle_id: str,
                              description: str, severity: str = "MEDIUM",
                              photo_urls: list = None):
        return self.post("/v1/damage-reports", {
            "rentalId": rental_id, "vehicleId": vehicle_id,
            "description": description, "severity": severity,
            "photoUrls": photo_urls or [],
        })

    def get_damage_report(self, report_id: str):
        return self.get(f"/v1/damage-reports/{report_id}")


# ------------------------------------------------------------------ #
#  Chain runner — collects steps, prints report                       #
# ------------------------------------------------------------------ #

class ChainRunner:
    """Collects step results and prints a structured report."""

    PASS = "PASS"
    FAIL = "FAIL"

    def __init__(self, name: str, verbose: bool = True):
        self.name = name
        self.verbose = verbose
        self._steps: list[dict] = []
        self._failed = False

    def step(self, title: str, status: int, body: dict,
             expected_status: int = None, expected_error: str = None,
             expected_fields: dict = None):
        """Record an API step."""
        checks = []
        ok = True

        if expected_status is not None:
            match = status == expected_status
            checks.append(("status", expected_status, status, match))
            if not match:
                ok = False

        if expected_error is not None:
            actual_err = body.get("error", "")
            match = actual_err == expected_error
            checks.append(("error_code", expected_error, actual_err, match))
            if not match:
                ok = False

        if expected_fields:
            for field, expected_val in expected_fields.items():
                actual_val = body.get(field)
                match = actual_val == expected_val
                checks.append((f"body.{field}", expected_val, actual_val, match))
                if not match:
                    ok = False

        if not ok:
            self._failed = True

        record = {"title": title, "status": status, "ok": ok, "checks": checks, "body": body}
        self._steps.append(record)

        if self.verbose:
            icon = "OK" if ok else "FAIL"
            print(f"    [{icon}] [{status}] {title}")
            for field, exp, act, m in checks:
                if not m:
                    print(f"        !! {field}: expected={exp!r}, got={act!r}")

        return ok, body

    def check(self, title: str, condition: bool, details: str = ""):
        """Record a pure assertion (no HTTP call)."""
        if not condition:
            self._failed = True
        record = {"title": title, "status": None, "ok": condition, "checks": [], "body": {}}
        self._steps.append(record)
        if self.verbose:
            icon = "OK" if condition else "FAIL"
            extra = f" ({details})" if details else ""
            print(f"    [{icon}] ASSERT {title}{extra}")
        return condition

    def result(self) -> bool:
        passed = not self._failed
        total = len(self._steps)
        failed = sum(1 for s in self._steps if not s["ok"])
        status = self.PASS if passed else self.FAIL
        if self.verbose:
            print(f"  [{status}] {self.name}  ({total - failed}/{total} checks)\n")
        return passed
