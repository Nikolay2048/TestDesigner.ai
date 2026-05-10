"""
Seed data covering all 15 test cases.

Pre-seeded IDs (for AI agent reference):

USERS:
  user-verified       — VERIFIED, valid license, active payment  (case 001, 005, 006, 007…)
  user-expired-lic    — VERIFIED, expired driver license          (case 003)
  user-blocked        — BLOCKED                                   (case 004)
  user-for-cancel     — VERIFIED, booking ready to cancel         (case 009)
  user-auto-cancel    — VERIFIED, booking ready for system expire  (case 010)
  user-with-rental    — VERIFIED, has STARTED rental              (case 011, 012)
  user-for-refund     — VERIFIED, has FINISHED rental + CAPTURED payment (case 015)
  user-for-fine       — VERIFIED, has FINISHED rental             (case 013, 014)
  user-for-retry      — VERIFIED, has FINISHED rental + FAILED payment  (case 008)

VEHICLES:
  vehicle-available-1..5 — AVAILABLE
  vehicle-reserved    — RESERVED (active booking by user-verified)  (case 005)
  vehicle-in-use      — IN_USE   (active rental by user-with-rental) (case 006, 011, 012)
  vehicle-maintenance — MAINTENANCE
  vehicle-blocked     — BLOCKED

BOOKINGS:
  booking-confirmed   — CONFIRMED, user-verified / vehicle-available-1
  booking-expired     — EXPIRED                                   (case 002)
  booking-to-cancel   — CONFIRMED, user-for-cancel / vehicle-available-2 (case 009)
  booking-to-expire   — CONFIRMED, user-auto-cancel / vehicle-available-3 (case 010)
  booking-reserved    — CONFIRMED, user-verified / vehicle-reserved  (case 005 existing)
  booking-started     — STARTED, user-with-rental / vehicle-in-use

RENTALS:
  rental-started      — STARTED, user-with-rental / vehicle-in-use
  rental-finished     — FINISHED, user-for-fine                   (case 013, 014)
  rental-for-refund   — FINISHED+PAID, user-for-refund            (case 015)
  rental-for-retry    — FINISHED, user-for-retry                  (case 008)

PAYMENTS:
  payment-failed      — FAILED, rental-for-retry                  (case 008)
  payment-captured    — CAPTURED, rental-for-refund               (case 015)
"""

from datetime import datetime, timezone, timedelta, date
from .models import (
    UserStatus, DriverLicenseStatus, VehicleStatus,
    BookingStatus, RentalStatus, PaymentStatus,
    FineStatus, DamageReportStatus,
)
from .state import store


def _ts(delta_hours: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


def _date_str(delta_days: int = 0) -> str:
    return (date.today() + timedelta(days=delta_days)).isoformat()


def seed_all():
    """Populate store with initial data and save snapshot for reset."""
    _seed_users()
    _seed_driver_licenses()
    _seed_payment_methods()
    _seed_vehicles()
    _seed_telemetry()
    _seed_bookings()
    _seed_rentals()
    _seed_payments()
    store.save_snapshot()


# ------------------------------------------------------------------ #
#  Users                                                              #
# ------------------------------------------------------------------ #

def _seed_users():
    users = {
        "user-verified": {
            "userId": "user-verified",
            "name": "Иван Иванов",
            "email": "ivan@example.com",
            "status": UserStatus.VERIFIED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
        "user-expired-lic": {
            "userId": "user-expired-lic",
            "name": "Пётр Петров",
            "email": "petr@example.com",
            "status": UserStatus.VERIFIED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
        "user-blocked": {
            "userId": "user-blocked",
            "name": "Сидор Сидоров",
            "email": "sidor@example.com",
            "status": UserStatus.BLOCKED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
        "user-for-cancel": {
            "userId": "user-for-cancel",
            "name": "Алексей Алексеев",
            "email": "alex@example.com",
            "status": UserStatus.VERIFIED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
        "user-auto-cancel": {
            "userId": "user-auto-cancel",
            "name": "Мария Маринова",
            "email": "maria@example.com",
            "status": UserStatus.VERIFIED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
        "user-with-rental": {
            "userId": "user-with-rental",
            "name": "Николай Николаев",
            "email": "nikolay@example.com",
            "status": UserStatus.VERIFIED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
        "user-for-refund": {
            "userId": "user-for-refund",
            "name": "Ольга Орлова",
            "email": "olga@example.com",
            "status": UserStatus.VERIFIED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
        "user-for-fine": {
            "userId": "user-for-fine",
            "name": "Дмитрий Дмитриев",
            "email": "dmitry@example.com",
            "status": UserStatus.VERIFIED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
        "user-for-retry": {
            "userId": "user-for-retry",
            "name": "Антон Антонов",
            "email": "anton@example.com",
            "status": UserStatus.VERIFIED,
            "has_critical_debt": False,
            "created_at": _ts(-720),
        },
    }
    store.db["users"] = users


# ------------------------------------------------------------------ #
#  Driver Licenses                                                     #
# ------------------------------------------------------------------ #

def _seed_driver_licenses():
    lics = {}
    valid_users = [
        "user-verified", "user-for-cancel", "user-auto-cancel",
        "user-with-rental", "user-for-refund", "user-for-fine", "user-for-retry",
    ]
    for uid in valid_users:
        lics[uid] = {
            "userId": uid,
            "licenseNumber": f"LIC-{uid.upper()[:8]}",
            "status": DriverLicenseStatus.VERIFIED,
            "expiration_date": _date_str(365 * 3),
            "verified_at": _ts(-30 * 24),
        }

    # user-expired-lic — expired yesterday
    lics["user-expired-lic"] = {
        "userId": "user-expired-lic",
        "licenseNumber": "LIC-EXPIRED",
        "status": DriverLicenseStatus.VERIFIED,
        "expiration_date": _date_str(-1),
        "verified_at": _ts(-365 * 24),
    }

    # user-blocked — rejected license (shouldn't matter, user is blocked)
    lics["user-blocked"] = {
        "userId": "user-blocked",
        "licenseNumber": "LIC-BLOCKED",
        "status": DriverLicenseStatus.REJECTED,
        "expiration_date": _date_str(365),
        "verified_at": None,
    }

    store.db["driver_licenses"] = lics


# ------------------------------------------------------------------ #
#  Payment Methods                                                     #
# ------------------------------------------------------------------ #

def _seed_payment_methods():
    valid_users = [
        "user-verified", "user-expired-lic", "user-for-cancel", "user-auto-cancel",
        "user-with-rental", "user-for-refund", "user-for-fine", "user-for-retry",
    ]
    methods = {}
    for uid in valid_users:
        methods[uid] = {
            "userId": uid,
            "cardLast4": "1234",
            "cardBrand": "VISA",
            "active": True,
            "provider_binding_id": f"binding-{uid}",
        }
    # user-blocked has no payment method
    store.db["payment_methods"] = methods


# ------------------------------------------------------------------ #
#  Vehicles                                                            #
# ------------------------------------------------------------------ #

def _seed_vehicles():
    vehicles = {}

    for i in range(1, 6):
        vid = f"vehicle-available-{i}"
        # Vehicles 1,2,3 are RESERVED (linked to pre-seeded confirmed bookings)
        # Vehicles 4,5 are AVAILABLE (free for test-dynamic bookings)
        veh_status = VehicleStatus.RESERVED if i <= 3 else VehicleStatus.AVAILABLE
        vehicles[vid] = {
            "vehicleId": vid,
            "brand": "Kia",
            "model": f"Rio-{i}",
            "plate": f"A{100 + i}BC77",
            "status": veh_status,
            "fuel_level": 80 - i * 5,
            "tariff_id": "tariff-standard",
            "tariff_per_minute": 8.0,
            "location": {"lat": 55.75 + i * 0.01, "lon": 37.62 + i * 0.01},
            "zone_id": "zone-moscow-center",
        }

    vehicles["vehicle-reserved"] = {
        "vehicleId": "vehicle-reserved",
        "brand": "Hyundai",
        "model": "Solaris",
        "plate": "B200KX77",
        "status": VehicleStatus.RESERVED,
        "fuel_level": 60,
        "tariff_id": "tariff-standard",
        "tariff_per_minute": 8.0,
        "location": {"lat": 55.76, "lon": 37.63},
        "zone_id": "zone-moscow-center",
    }

    vehicles["vehicle-in-use"] = {
        "vehicleId": "vehicle-in-use",
        "brand": "Volkswagen",
        "model": "Polo",
        "plate": "C300MH77",
        "status": VehicleStatus.IN_USE,
        "fuel_level": 45,
        "tariff_id": "tariff-standard",
        "tariff_per_minute": 8.0,
        "location": {"lat": 55.77, "lon": 37.64},
        "zone_id": "zone-moscow-center",
    }

    vehicles["vehicle-maintenance"] = {
        "vehicleId": "vehicle-maintenance",
        "brand": "Skoda",
        "model": "Rapid",
        "plate": "D400PQ77",
        "status": VehicleStatus.MAINTENANCE,
        "fuel_level": 30,
        "tariff_id": "tariff-standard",
        "tariff_per_minute": 8.0,
        "location": {"lat": 55.78, "lon": 37.65},
        "zone_id": "zone-moscow-center",
    }

    vehicles["vehicle-blocked"] = {
        "vehicleId": "vehicle-blocked",
        "brand": "Renault",
        "model": "Logan",
        "plate": "E500RS77",
        "status": VehicleStatus.BLOCKED,
        "fuel_level": 20,
        "tariff_id": "tariff-standard",
        "tariff_per_minute": 8.0,
        "location": {"lat": 55.79, "lon": 37.66},
        "zone_id": "zone-moscow-center",
    }

    store.db["vehicles"] = vehicles


# ------------------------------------------------------------------ #
#  Telemetry                                                           #
# ------------------------------------------------------------------ #

def _seed_telemetry():
    telemetry = {}
    for i in range(1, 6):
        vid = f"vehicle-available-{i}"
        telemetry[vid] = {
            "vehicleId": vid,
            "lat": 55.75 + i * 0.01,
            "lon": 37.62 + i * 0.01,
            "speed_kmh": 0,
            "fuel_level": 80 - i * 5,
            "engine_on": False,
            "doors_closed": True,
            "updated_at": _ts(-5),
            "stale": False,
        }
    telemetry["vehicle-in-use"] = {
        "vehicleId": "vehicle-in-use",
        "lat": 55.77,
        "lon": 37.64,
        "speed_kmh": 0,
        "fuel_level": 45,
        "engine_on": False,
        "doors_closed": True,
        "updated_at": _ts(-2),
        "stale": False,
    }
    telemetry["vehicle-reserved"] = {
        "vehicleId": "vehicle-reserved",
        "lat": 55.76,
        "lon": 37.63,
        "speed_kmh": 0,
        "fuel_level": 60,
        "engine_on": False,
        "doors_closed": True,
        "updated_at": _ts(-3),
        "stale": False,
    }
    store.db["vehicle_telemetry"] = telemetry


# ------------------------------------------------------------------ #
#  Bookings                                                            #
# ------------------------------------------------------------------ #

def _seed_bookings():
    bookings = {
        "booking-confirmed": {
            "bookingId": "booking-confirmed",
            "userId": "user-verified",
            "vehicleId": "vehicle-available-1",
            "status": BookingStatus.CONFIRMED,
            "created_at": _ts(-10),
            "expires_at": _ts(5),
        },
        "booking-expired": {
            "bookingId": "booking-expired",
            "userId": "user-verified",
            "vehicleId": "vehicle-available-4",
            "status": BookingStatus.EXPIRED,
            "created_at": _ts(-30),
            "expires_at": _ts(-15),
        },
        "booking-to-cancel": {
            "bookingId": "booking-to-cancel",
            "userId": "user-for-cancel",
            "vehicleId": "vehicle-available-2",
            "status": BookingStatus.CONFIRMED,
            "created_at": _ts(-5),
            "expires_at": _ts(10),
        },
        "booking-to-expire": {
            "bookingId": "booking-to-expire",
            "userId": "user-auto-cancel",
            "vehicleId": "vehicle-available-3",
            "status": BookingStatus.CONFIRMED,
            "created_at": _ts(-20),
            "expires_at": _ts(-5),  # already past TTL — system should expire
        },
        "booking-reserved": {
            "bookingId": "booking-reserved",
            "userId": "user-verified",
            "vehicleId": "vehicle-reserved",
            "status": BookingStatus.CONFIRMED,
            "created_at": _ts(-8),
            "expires_at": _ts(7),
        },
        "booking-started": {
            "bookingId": "booking-started",
            "userId": "user-with-rental",
            "vehicleId": "vehicle-in-use",
            "status": BookingStatus.STARTED,
            "created_at": _ts(-60),
            "expires_at": _ts(900),
        },
    }
    store.db["bookings"] = bookings


# ------------------------------------------------------------------ #
#  Rentals                                                             #
# ------------------------------------------------------------------ #

def _seed_rentals():
    rentals = {
        "rental-started": {
            "rentalId": "rental-started",
            "bookingId": "booking-started",
            "userId": "user-with-rental",
            "vehicleId": "vehicle-in-use",
            "status": RentalStatus.STARTED,
            "started_at": _ts(-45),
            "finished_at": None,
            "tariff_per_minute": 8.0,
        },
        "rental-finished": {
            "rentalId": "rental-finished",
            "bookingId": None,
            "userId": "user-for-fine",
            "vehicleId": "vehicle-available-5",
            "status": RentalStatus.FINISHED,
            "started_at": _ts(-10 * 24),
            "finished_at": _ts(-10 * 24 + 1),
            "tariff_per_minute": 8.0,
            "total_minutes": 60,
            "amount": 480.0,
        },
        "rental-for-refund": {
            "rentalId": "rental-for-refund",
            "bookingId": None,
            "userId": "user-for-refund",
            "vehicleId": "vehicle-available-5",
            "status": RentalStatus.PAID,
            "started_at": _ts(-5 * 24),
            "finished_at": _ts(-5 * 24 + 1),
            "tariff_per_minute": 8.0,
            "total_minutes": 30,
            "amount": 240.0,
        },
        "rental-for-retry": {
            "rentalId": "rental-for-retry",
            "bookingId": None,
            "userId": "user-for-retry",
            "vehicleId": "vehicle-available-5",
            "status": RentalStatus.PAYMENT_FAILED,
            "started_at": _ts(-3 * 24),
            "finished_at": _ts(-3 * 24 + 1),
            "tariff_per_minute": 8.0,
            "total_minutes": 45,
            "amount": 360.0,
        },
    }
    store.db["rental_sessions"] = rentals


# ------------------------------------------------------------------ #
#  Payments                                                            #
# ------------------------------------------------------------------ #

def _seed_payments():
    payments = {
        "payment-failed": {
            "paymentId": "payment-failed",
            "rentalId": "rental-for-retry",
            "userId": "user-for-retry",
            "amount": 360.0,
            "currency": "RUB",
            "status": PaymentStatus.FAILED,
            "retry_count": 0,
            "created_at": _ts(-3 * 24 + 2),
            "updated_at": _ts(-3 * 24 + 2),
            "failure_reason": "PROVIDER_TIMEOUT",
        },
        "payment-captured": {
            "paymentId": "payment-captured",
            "rentalId": "rental-for-refund",
            "userId": "user-for-refund",
            "amount": 240.0,
            "currency": "RUB",
            "status": PaymentStatus.CAPTURED,
            "retry_count": 0,
            "created_at": _ts(-5 * 24 + 2),
            "updated_at": _ts(-5 * 24 + 2),
        },
    }
    store.db["payments"] = payments
