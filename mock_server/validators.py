"""
FLK (ФЛК) — business validation rules.

Each function returns (ok: bool, error_code: str, http_status: int, message: str).
"""

from datetime import datetime, timezone, date
from .state import store
from .models import (
    UserStatus, DriverLicenseStatus, VehicleStatus,
    BookingStatus, RentalStatus, PaymentStatus,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().date()


# ------------------------------------------------------------------ #
#  FLK-USER-001  User must exist                                       #
# ------------------------------------------------------------------ #
def flk_user_exists(user_id: str):
    if user_id not in store.db["users"]:
        return False, "USER_NOT_FOUND", 404, f"User '{user_id}' not found"
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-USER-002  User must be VERIFIED                                 #
# ------------------------------------------------------------------ #
def flk_user_verified(user_id: str):
    ok, *rest = flk_user_exists(user_id)
    if not ok:
        return False, *rest
    user = store.db["users"][user_id]
    if user["status"] != UserStatus.VERIFIED:
        return False, "USER_NOT_VERIFIED", 409, (
            f"User '{user_id}' is not VERIFIED (current: {user['status']})"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-USER-003  User must not be BLOCKED                             #
# ------------------------------------------------------------------ #
def flk_user_not_blocked(user_id: str):
    ok, *rest = flk_user_exists(user_id)
    if not ok:
        return False, *rest
    user = store.db["users"][user_id]
    if user["status"] == UserStatus.BLOCKED:
        return False, "USER_BLOCKED", 409, f"User '{user_id}' is BLOCKED"
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-USER-FULL  Combined user check (exists + verified + not blocked)
# ------------------------------------------------------------------ #
def flk_user_full(user_id: str):
    for check in (flk_user_exists, flk_user_not_blocked, flk_user_verified):
        ok, code, status, msg = check(user_id)
        if not ok:
            return False, code, status, msg
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-USER-NO-ACTIVE-DEBT  No critical debt                          #
# ------------------------------------------------------------------ #
def flk_user_no_critical_debt(user_id: str):
    user = store.db["users"].get(user_id, {})
    if user.get("has_critical_debt", False):
        return False, "USER_HAS_CRITICAL_DEBT", 409, f"User '{user_id}' has critical debt"
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-USER-NO-ACTIVE-RENTAL                                          #
# ------------------------------------------------------------------ #
def flk_user_no_active_rental(user_id: str):
    for r in store.db["rental_sessions"].values():
        if r["userId"] == user_id and r["status"] == RentalStatus.STARTED:
            return False, "USER_HAS_ACTIVE_RENTAL", 409, (
                f"User '{user_id}' already has active rental '{r['rentalId']}'"
            )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-LICENSE-001  Driver license must be VERIFIED and not expired   #
# ------------------------------------------------------------------ #
def flk_license_valid(user_id: str):
    lic = store.db["driver_licenses"].get(user_id)
    if lic is None:
        return False, "DRIVER_LICENSE_NOT_FOUND", 409, (
            f"Driver license for user '{user_id}' not found"
        )
    if lic["status"] == DriverLicenseStatus.REJECTED:
        return False, "DRIVER_LICENSE_REJECTED", 409, (
            f"Driver license for user '{user_id}' is REJECTED"
        )
    if lic["status"] != DriverLicenseStatus.VERIFIED:
        return False, "DRIVER_LICENSE_INVALID", 409, (
            f"Driver license for user '{user_id}' is not VERIFIED (current: {lic['status']})"
        )
    exp_date = date.fromisoformat(lic["expiration_date"])
    if exp_date <= _today():
        return False, "DRIVER_LICENSE_EXPIRED", 409, (
            f"Driver license for user '{user_id}' expired on {lic['expiration_date']}"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-PAYMENT-METHOD  Active payment method must exist               #
# ------------------------------------------------------------------ #
def flk_payment_method_exists(user_id: str):
    pm = store.db["payment_methods"].get(user_id)
    if pm is None or not pm.get("active", False):
        return False, "PAYMENT_METHOD_NOT_FOUND", 409, (
            f"No active payment method for user '{user_id}'"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-VEHICLE-001  Vehicle must be AVAILABLE                         #
# ------------------------------------------------------------------ #
def flk_vehicle_available(vehicle_id: str):
    v = store.db["vehicles"].get(vehicle_id)
    if v is None:
        return False, "VEHICLE_NOT_FOUND", 404, f"Vehicle '{vehicle_id}' not found"
    if v["status"] != VehicleStatus.AVAILABLE:
        return False, "VEHICLE_NOT_AVAILABLE", 409, (
            f"Vehicle '{vehicle_id}' is not AVAILABLE (current: {v['status']})"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-VEHICLE-RESERVED  Vehicle must be RESERVED (for rental start)  #
# ------------------------------------------------------------------ #
def flk_vehicle_reserved(vehicle_id: str):
    v = store.db["vehicles"].get(vehicle_id)
    if v is None:
        return False, "VEHICLE_NOT_FOUND", 404, f"Vehicle '{vehicle_id}' not found"
    if v["status"] != VehicleStatus.RESERVED:
        return False, "VEHICLE_NOT_RESERVED", 409, (
            f"Vehicle '{vehicle_id}' is not RESERVED (current: {v['status']})"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-BOOKING-001  Booking must be in CONFIRMED or STARTED           #
# ------------------------------------------------------------------ #
def flk_booking_active(booking_id: str):
    b = store.db["bookings"].get(booking_id)
    if b is None:
        return False, "BOOKING_NOT_FOUND", 404, f"Booking '{booking_id}' not found"
    if b["status"] not in (BookingStatus.CONFIRMED, BookingStatus.STARTED):
        return False, "BOOKING_STATUS_INVALID", 409, (
            f"Booking '{booking_id}' status is '{b['status']}', expected CONFIRMED or STARTED"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-BOOKING-CONFIRMED  Booking must be CONFIRMED (for start)       #
# ------------------------------------------------------------------ #
def flk_booking_confirmed(booking_id: str):
    b = store.db["bookings"].get(booking_id)
    if b is None:
        return False, "BOOKING_NOT_FOUND", 404, f"Booking '{booking_id}' not found"
    if b["status"] != BookingStatus.CONFIRMED:
        return False, "BOOKING_STATUS_INVALID", 409, (
            f"Booking '{booking_id}' status is '{b['status']}', expected CONFIRMED"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-BOOKING-NOT-EXPIRED  Booking TTL not exceeded                  #
# ------------------------------------------------------------------ #
def flk_booking_not_expired(booking_id: str):
    b = store.db["bookings"].get(booking_id)
    if b is None:
        return False, "BOOKING_NOT_FOUND", 404, f"Booking '{booking_id}' not found"
    exp = b.get("expires_at")
    if exp:
        exp_dt = datetime.fromisoformat(exp)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        if _now() > exp_dt:
            return False, "BOOKING_EXPIRED", 409, (
                f"Booking '{booking_id}' has expired at {exp}"
            )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-RENTAL-001  Rental session must exist                          #
# ------------------------------------------------------------------ #
def flk_rental_exists(rental_id: str):
    if rental_id not in store.db["rental_sessions"]:
        return False, "RENTAL_NOT_FOUND", 404, f"Rental '{rental_id}' not found"
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-RENTAL-STARTED  Rental must be in STARTED status               #
# ------------------------------------------------------------------ #
def flk_rental_started(rental_id: str):
    ok, *rest = flk_rental_exists(rental_id)
    if not ok:
        return False, *rest
    r = store.db["rental_sessions"][rental_id]
    if r["status"] != RentalStatus.STARTED:
        return False, "RENTAL_STATUS_INVALID", 409, (
            f"Rental '{rental_id}' is not STARTED (current: {r['status']})"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-PAYMENT-001  No duplicate payments                             #
# ------------------------------------------------------------------ #
def flk_no_duplicate_payment(rental_id: str):
    for p in store.db["payments"].values():
        if p["rentalId"] == rental_id and p["status"] in (
            PaymentStatus.CAPTURED, PaymentStatus.PAID
        ):
            return False, "PAYMENT_OPERATION_DUPLICATE", 409, (
                f"Successful payment already exists for rental '{rental_id}'"
            )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-PAYMENT-EXISTS                                                  #
# ------------------------------------------------------------------ #
def flk_payment_exists(payment_id: str):
    if payment_id not in store.db["payments"]:
        return False, "PAYMENT_NOT_FOUND", 404, f"Payment '{payment_id}' not found"
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-PAYMENT-FAILED  Payment must be FAILED to retry                #
# ------------------------------------------------------------------ #
def flk_payment_failed(payment_id: str):
    ok, *rest = flk_payment_exists(payment_id)
    if not ok:
        return False, *rest
    p = store.db["payments"][payment_id]
    if p["status"] != PaymentStatus.FAILED:
        return False, "PAYMENT_NOT_FAILED", 409, (
            f"Payment '{payment_id}' is not FAILED (current: {p['status']})"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-PAYMENT-CAPTURABLE  Payment can be refunded                    #
# ------------------------------------------------------------------ #
def flk_payment_capturable(payment_id: str):
    ok, *rest = flk_payment_exists(payment_id)
    if not ok:
        return False, *rest
    p = store.db["payments"][payment_id]
    # Check REFUNDED first so error code is specific
    if p["status"] == PaymentStatus.REFUNDED:
        return False, "PAYMENT_ALREADY_REFUNDED", 409, (
            f"Payment '{payment_id}' is already REFUNDED"
        )
    if p["status"] not in (PaymentStatus.CAPTURED, PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED):
        return False, "PAYMENT_NOT_CAPTURABLE", 409, (
            f"Payment '{payment_id}' cannot be refunded (status: {p['status']})"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-ZONE  Vehicle must be in allowed finish zone                   #
# ------------------------------------------------------------------ #
def flk_vehicle_in_zone(vehicle_id: str):
    if vehicle_id in store.mock_config["vehicles_outside_zone"]:
        return False, "VEHICLE_OUTSIDE_ALLOWED_ZONE", 409, (
            f"Vehicle '{vehicle_id}' is outside the allowed finish zone"
        )
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-GEO  Geo service availability                                  #
# ------------------------------------------------------------------ #
def flk_geo_service_available():
    if not store.mock_config["geo_service_available"]:
        return False, "GEO_SERVICE_UNAVAILABLE", 503, "Geo service is currently unavailable"
    return True, None, None, None


# ------------------------------------------------------------------ #
#  FLK-RETRY-LIMIT  Max retry attempts not exceeded                   #
# ------------------------------------------------------------------ #
MAX_RETRY_ATTEMPTS = 3


def flk_retry_limit_not_exceeded(payment_id: str):
    p = store.db["payments"].get(payment_id, {})
    attempts = p.get("retry_count", 0)
    if attempts >= MAX_RETRY_ATTEMPTS:
        return False, "PAYMENT_RETRY_LIMIT_EXCEEDED", 409, (
            f"Payment '{payment_id}' exceeded max retry attempts ({MAX_RETRY_ATTEMPTS})"
        )
    return True, None, None, None
