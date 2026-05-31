"""FastAPI mock-сервер каршеринга.

Реализует UC_001 / UC_002 / UC_003 и вспомогательные endpoints.
Endpoints соответствуют YAML-спекам в data/scenarios/openapi_spec/.

Seed-данные:
  car COMFORT cityId=77  → 11111111-...
  car SUV     cityId=77  → 22222222-... (статус MAINTENANCE — недоступен)
  car ECONOMY cityId=36  → 33333333-...
  car PREMIUM cityId=36  → 44444444-... (статус RETIRED — недоступен)

  customer ACTIVE  → aaaaaaaa-...
  customer BLOCKED → bbbbbbbb-...

  payment CONFIRMED   → 99999999-...
  payment AUTHORIZED  → 77777777-...
  payment DECLINED    → 88888888-...
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import FastAPI, Query, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Carsharing Mock Server", version="1.0.0")

ALLOWED_CITY_IDS = {36, 77}
CAR_CLASSES = {"ECONOMY", "COMFORT", "BUSINESS", "SUV", "PREMIUM"}
TARIFFS = {
    "BASE": Decimal("1.00"),
    "FULL_INSURANCE": Decimal("1.25"),
    "PREMIUM": Decimal("1.60"),
}
CURRENCIES = {"RUB", "USD", "EUR"}
ACTIVE_DRAFT_LIMIT = 3
DRAFT_TTL_MINUTES = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"errorCode": code, "message": message})


def _overlaps(a_from: datetime, a_to: datetime, b_from: datetime, b_to: datetime) -> bool:
    return a_from < b_to and b_from < a_to


def _serialize(obj: Any) -> Any:
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj


# ─────────────────────────── In-memory DB ───────────────────────────────────

DB: dict[str, Any] = {}


def _seed() -> None:
    c1 = UUID("11111111-1111-1111-1111-111111111111")
    c2 = UUID("22222222-2222-2222-2222-222222222222")
    c3 = UUID("33333333-3333-3333-3333-333333333333")
    c4 = UUID("44444444-4444-4444-4444-444444444444")
    cust_active = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    cust_blocked = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    pay_ok = UUID("99999999-9999-9999-9999-999999999999")
    pay_auth = UUID("77777777-7777-7777-7777-777777777777")
    pay_decl = UUID("88888888-8888-8888-8888-888888888888")

    DB.clear()
    DB.update({
        "cars": {
            c1: {"carId": c1, "cityId": 77, "carClass": "COMFORT",  "status": "AVAILABLE",   "dailyRate": Decimal("4500")},
            c2: {"carId": c2, "cityId": 77, "carClass": "SUV",      "status": "MAINTENANCE", "dailyRate": Decimal("5200")},
            c3: {"carId": c3, "cityId": 36, "carClass": "ECONOMY",  "status": "AVAILABLE",   "dailyRate": Decimal("2700")},
            c4: {"carId": c4, "cityId": 36, "carClass": "PREMIUM",  "status": "RETIRED",     "dailyRate": Decimal("9000")},
        },
        "customers": {
            cust_active:  {"customerId": cust_active,  "status": "ACTIVE"},
            cust_blocked: {"customerId": cust_blocked, "status": "BLOCKED"},
        },
        "promoCodes": {
            "WELCOME10": {"status": "ACTIVE",   "discountPercent": Decimal("10"), "expiresAt": _now() + timedelta(days=30)},
            "EXPIRED10": {"status": "EXPIRED",  "discountPercent": Decimal("10"), "expiresAt": _now() - timedelta(days=1)},
        },
        "payments": {
            pay_ok:   {"paymentId": pay_ok,   "status": "CONFIRMED"},
            pay_auth: {"paymentId": pay_auth, "status": "AUTHORIZED"},
            pay_decl: {"paymentId": pay_decl, "status": "DECLINED"},
        },
        "priceCalculations": {},
        "drafts": {},
        "reservations": {},
        "locks": {},
        "cancellation_policies": {},
        "cancellations": {},
        "refunds": {},
    })

    # ── Seed: pre-confirmed reservation for UC_008 standalone testing ─────────
    res_seed_id = UUID("cccccccc-0000-0000-0000-cccccccccccc")
    lock_seed_id = UUID("eeeeeeee-0000-0000-0000-eeeeeeeeeeee")
    from datetime import timezone as _tz
    seed_from = datetime(2099, 12, 1, 10, 0, 0, tzinfo=_tz.utc)
    seed_to   = datetime(2099, 12, 7, 10, 0, 0, tzinfo=_tz.utc)
    seed_deposit = Decimal("4050.00")
    DB["reservations"][res_seed_id] = {
        "reservationId": res_seed_id,
        "reservationDraftId": UUID("dddddddd-0000-0000-0000-dddddddddddd"),
        "status": "CONFIRMED",
        "carId": c1, "customerId": cust_active, "cityId": 77,
        "dateFrom": seed_from, "dateTo": seed_to,
        "depositAmount": seed_deposit, "currency": "RUB",
    }
    DB["locks"][lock_seed_id] = {
        "lockId": lock_seed_id, "carId": c1, "lockType": "RENTAL_RESERVATION",
        "dateFrom": seed_from, "dateTo": seed_to, "status": "ACTIVE",
    }


_seed()


def _is_car_free(car_id: UUID, date_from: datetime, date_to: datetime,
                 exclude_draft_id: Optional[UUID] = None) -> bool:
    car = DB["cars"].get(car_id)
    if not car or car["status"] != "AVAILABLE":
        return False
    for draft in DB["drafts"].values():
        if exclude_draft_id and draft.get("reservationDraftId") == exclude_draft_id:
            continue
        if draft["carId"] == car_id and draft["status"] == "DRAFT" and draft["expiresAt"] > _now():
            if _overlaps(date_from, date_to, draft["dateFrom"], draft["dateTo"]):
                return False
    for lock in DB["locks"].values():
        if lock["carId"] == car_id and lock["status"] == "ACTIVE":
            if _overlaps(date_from, date_to, lock["dateFrom"], lock["dateTo"]):
                return False
    return True


# ──────────────── UC_001: поиск доступных авто ──────────────────────────────

@app.get("/api/v1/cars/availability")
def search_available_cars(
    cityId: Optional[int] = Query(None),
    dateFrom: Optional[str] = Query(None),
    dateTo: Optional[str] = Query(None),
    driverAge: Optional[int] = Query(None),
    carClass: Optional[str] = Query(None),
):
    df, dt = _parse_dt(dateFrom), _parse_dt(dateTo)

    if cityId is None:             return _err(400, "CAR-AVAILABILITY-001", "cityId is required")
    if cityId not in ALLOWED_CITY_IDS: return _err(400, "CAR-AVAILABILITY-002", "cityId must be 36 or 77")
    if df is None:                 return _err(400, "CAR-AVAILABILITY-003", "dateFrom is required or invalid")
    if dt is None:                 return _err(400, "CAR-AVAILABILITY-004", "dateTo is required or invalid")
    if df < _now():                return _err(400, "CAR-AVAILABILITY-005", "dateFrom must not be in the past")
    if dt <= df:                   return _err(400, "CAR-AVAILABILITY-006", "dateTo must be greater than dateFrom")
    if driverAge is None:          return _err(400, "CAR-AVAILABILITY-009", "driverAge is required")
    if driverAge < 21:             return _err(400, "CAR-AVAILABILITY-010", "driverAge must be at least 21")
    if driverAge > 75:             return _err(400, "CAR-AVAILABILITY-011", "driverAge must not exceed 75")
    if carClass and carClass not in CAR_CLASSES:
        return _err(400, "CAR-AVAILABILITY-012", "carClass is not supported")

    items = [
        car for car in DB["cars"].values()
        if car["cityId"] == cityId
        and (not carClass or car["carClass"] == carClass)
        and _is_car_free(car["carId"], df, dt)
    ]
    return _serialize({"items": items})


# ──────────────── UC_002: создание черновика брони ──────────────────────────

class CreateDraftRequest(BaseModel):
    carId: Optional[UUID] = None
    cityId: Optional[int] = None
    customerId: Optional[UUID] = None
    dateFrom: Optional[datetime] = None
    dateTo: Optional[datetime] = None
    tariffCode: Optional[str] = None
    promoCode: Optional[str] = None


@app.post("/api/v1/reservations/drafts", status_code=201)
def create_draft(req: CreateDraftRequest):
    if req.carId is None:      return _err(400, "RESERVATION-DRAFT-001", "carId is required")
    car = DB["cars"].get(req.carId)
    if not car:                return _err(404, "RESERVATION-DRAFT-002", "carId not found")
    if req.cityId is None:     return _err(400, "RESERVATION-DRAFT-005", "cityId is required")
    if req.cityId not in ALLOWED_CITY_IDS: return _err(400, "RESERVATION-DRAFT-004", "cityId must be 36 or 77")
    if req.cityId != car["cityId"]: return _err(422, "RESERVATION-DRAFT-017", "cityId does not match the car's city")
    if req.customerId is None: return _err(400, "RESERVATION-DRAFT-006", "customerId is required")
    customer = DB["customers"].get(req.customerId)
    if not customer:           return _err(404, "RESERVATION-DRAFT-007", "customer not found")
    if customer["status"] != "ACTIVE": return _err(409, "RESERVATION-DRAFT-008", "customer is not active")
    if req.dateFrom is None or req.dateTo is None:
        return _err(400, "RESERVATION-DRAFT-009", "dateFrom and dateTo are required")
    df = req.dateFrom.astimezone(timezone.utc)
    dt = req.dateTo.astimezone(timezone.utc)
    if dt <= df:               return _err(400, "RESERVATION-DRAFT-010", "dateTo must be greater than dateFrom")
    if req.tariffCode is None: return _err(400, "RESERVATION-DRAFT-011", "tariffCode is required")
    if req.tariffCode not in TARIFFS: return _err(400, "RESERVATION-DRAFT-012", "tariffCode is not supported")
    if req.promoCode and req.promoCode not in DB["promoCodes"]:
        return _err(400, "RESERVATION-DRAFT-013", "promoCode is invalid")
    if car["status"] != "AVAILABLE": return _err(409, "RESERVATION-DRAFT-014", "car is unavailable by status")
    if not _is_car_free(req.carId, df, dt):
        return _err(409, "RESERVATION-DRAFT-015", "car has intersecting reservation")

    active_drafts = [
        d for d in DB["drafts"].values()
        if d["customerId"] == req.customerId and d["status"] == "DRAFT" and d["expiresAt"] > _now()
    ]
    if len(active_drafts) >= ACTIVE_DRAFT_LIMIT:
        return _err(409, "RESERVATION-DRAFT-016", "customer has too many active drafts")

    days = Decimal(str((dt - df).total_seconds() / 86400))
    total = (car["dailyRate"] * days * TARIFFS[req.tariffCode]).quantize(Decimal("0.01"))
    deposit = (total * Decimal("0.20")).quantize(Decimal("0.01"))

    draft_id = uuid4()
    DB["drafts"][draft_id] = {
        "reservationDraftId": draft_id, "status": "DRAFT",
        "expiresAt": _now() + timedelta(minutes=DRAFT_TTL_MINUTES),
        "carId": req.carId, "cityId": req.cityId, "customerId": req.customerId,
        "dateFrom": df, "dateTo": dt, "tariffCode": req.tariffCode, "promoCode": req.promoCode,
        "depositAmount": deposit, "totalAmount": total, "currency": "RUB",
    }
    return _serialize({
        "reservationDraftId": draft_id, "status": "DRAFT",
        "depositAmount": deposit, "currency": "RUB",
        "expiresAt": DB["drafts"][draft_id]["expiresAt"],
    })


# ──────────────── UC_003: подтверждение брони ───────────────────────────────

class ConfirmRequest(BaseModel):
    paymentId: Optional[UUID] = None
    paymentAmount: Optional[Decimal] = None
    currency: Optional[str] = None


@app.post("/api/v1/reservations/{reservationDraftId}/confirm")
def confirm_reservation(reservationDraftId: UUID = Path(...), req: ConfirmRequest = ...):
    draft = DB["drafts"].get(reservationDraftId)
    if not draft:                  return _err(404, "RESERVATION-CONFIRM-001", "reservationDraftId not found")
    if draft["status"] != "DRAFT": return _err(409, "RESERVATION-CONFIRM-002", "reservation is not in DRAFT status")
    if draft["expiresAt"] <= _now():
        draft["status"] = "EXPIRED"
        return _err(410, "RESERVATION-CONFIRM-003", "reservation draft expired")
    if req.paymentId is None:      return _err(400, "RESERVATION-CONFIRM-004", "paymentId is required")
    payment = DB["payments"].get(req.paymentId)
    if not payment:                return _err(404, "RESERVATION-CONFIRM-005", "payment not found")
    if req.paymentAmount != draft["depositAmount"]:
        return _err(400, "RESERVATION-CONFIRM-006", "paymentAmount does not match deposit")
    if req.currency not in CURRENCIES: return _err(400, "RESERVATION-CONFIRM-007", "currency is not supported")
    if payment["status"] == "DECLINED": return _err(409, "RESERVATION-CONFIRM-008", "payment is declined")
    if payment["status"] not in {"AUTHORIZED", "CONFIRMED"}:
        return _err(409, "RESERVATION-CONFIRM-010", "deposit is not confirmed")
    if not _is_car_free(draft["carId"], draft["dateFrom"], draft["dateTo"],
                        exclude_draft_id=reservationDraftId):
        return _err(409, "RESERVATION-CONFIRM-011", "car is already locked or unavailable")

    reservation_id, lock_id = uuid4(), uuid4()
    draft["status"] = "CONFIRMED"
    DB["reservations"][reservation_id] = {
        "reservationId": reservation_id, "reservationDraftId": reservationDraftId,
        "status": "CONFIRMED", "carId": draft["carId"],
        "customerId": draft["customerId"], "cityId": draft["cityId"],
        "dateFrom": draft["dateFrom"], "dateTo": draft["dateTo"],
        "depositAmount": draft["depositAmount"], "currency": draft["currency"],
    }
    DB["locks"][lock_id] = {
        "lockId": lock_id, "carId": draft["carId"], "lockType": "RENTAL_RESERVATION",
        "dateFrom": draft["dateFrom"], "dateTo": draft["dateTo"], "status": "ACTIVE",
    }
    return _serialize({
        "reservationId": reservation_id, "reservationDraftId": reservationDraftId,
        "status": "CONFIRMED",
        "carLock": DB["locks"][lock_id],
    })


# ──────────────── UC_008: отмена бронирования ───────────────────────────────

CANCEL_REASONS = {"PLANS_CHANGED", "EMERGENCY", "FOUND_BETTER_OPTION", "OTHER"}


@app.get("/api/v1/reservations/{reservationId}")
def get_reservation(reservationId: UUID = Path(...)):
    r = DB["reservations"].get(reservationId)
    if not r:
        return _err(404, "RESERVATION-GET-001", "reservation not found")
    return _serialize(r)


@app.get("/api/v1/reservations/{reservationId}/cancellation-policy")
def get_cancellation_policy(reservationId: UUID = Path(...)):
    r = DB["reservations"].get(reservationId)
    if not r:
        return _err(404, "RESERVATION-GET-001", "reservation not found")
    if r["status"] != "CONFIRMED":
        return _err(422, "CANCEL-010", "only CONFIRMED reservations can be cancelled")

    hours_until = (r["dateFrom"] - _now()).total_seconds() / 3600
    deposit = r["depositAmount"]

    if hours_until > 48:
        tier, fee_pct = "FREE", Decimal("0")
        fee_amount, refund = Decimal("0"), deposit
        desc = "Free cancellation: more than 48 hours before pickup"
    elif hours_until > 24:
        tier, fee_pct = "PARTIAL", Decimal("50")
        fee_amount = (deposit * Decimal("0.5")).quantize(Decimal("0.01"))
        refund = deposit - fee_amount
        desc = "Partial refund: 24–48 hours before pickup"
    else:
        tier, fee_pct = "NO_REFUND", Decimal("100")
        fee_amount, refund = deposit, Decimal("0")
        desc = "No refund: less than 24 hours before pickup"

    policy_id = uuid4()
    DB["cancellation_policies"][policy_id] = {
        "cancellationPolicyId": policy_id,
        "reservationId": reservationId,
        "policyTier": tier,
        "createdAt": _now(),
    }

    return _serialize({
        "cancellationPolicyId": policy_id,
        "policyTier": tier,
        "hoursUntilPickup": round(max(hours_until, 0.0), 2),
        "cancellationFeePercent": float(fee_pct),
        "cancellationFeeAmount": float(fee_amount),
        "refundAmount": float(refund),
        "currency": r["currency"],
        "policyDescription": desc,
    })


class CancelReservationRequest(BaseModel):
    cancellationPolicyId: Optional[UUID] = None
    reason: Optional[str] = None


@app.post("/api/v1/reservations/{reservationId}/cancel")
def cancel_reservation(reservationId: UUID = Path(...), req: CancelReservationRequest = ...):
    r = DB["reservations"].get(reservationId)
    if not r:
        return _err(404, "RESERVATION-GET-001", "reservation not found")
    if r["status"] == "CANCELLED":
        return _err(409, "CANCEL-009", "reservation is already cancelled")
    if r["status"] != "CONFIRMED":
        return _err(422, "CANCEL-010", "only CONFIRMED reservations can be cancelled")
    if req.cancellationPolicyId is None:
        return _err(400, "CANCEL-001", "cancellationPolicyId is required")
    if req.reason is None:
        return _err(400, "CANCEL-002", "reason is required")
    if req.reason not in CANCEL_REASONS:
        return _err(400, "CANCEL-003", f"reason must be one of {sorted(CANCEL_REASONS)}")

    policy = DB["cancellation_policies"].get(req.cancellationPolicyId)
    if not policy or policy["reservationId"] != reservationId:
        return _err(409, "CANCEL-012", "cancellationPolicyId does not match the current reservation")

    # Пересчёт суммы возврата по текущему времени (граница уровня могла сдвинуться)
    hours_until = (r["dateFrom"] - _now()).total_seconds() / 3600
    deposit = r["depositAmount"]
    if hours_until > 48:
        refund = deposit
    elif hours_until > 24:
        refund = (deposit * Decimal("0.5")).quantize(Decimal("0.01"))
    else:
        refund = Decimal("0")

    r["status"] = "CANCELLED"
    for lock in DB["locks"].values():
        if lock["carId"] == r["carId"] and lock["status"] == "ACTIVE":
            lock["status"] = "RELEASED"

    cancellation_id = uuid4()
    cancelled_at = _now()
    DB["cancellations"][cancellation_id] = {
        "cancellationId": cancellation_id, "reservationId": reservationId,
        "status": "CANCELLED", "refundAmount": refund,
        "currency": r["currency"], "cancelledAt": cancelled_at,
    }

    refund_id = uuid4()
    DB["refunds"][refund_id] = {
        "refundId": refund_id, "cancellationId": cancellation_id,
        "refundAmount": refund, "currency": r["currency"],
        "refundStatus": "PENDING" if refund > 0 else "COMPLETED",
        "estimatedArrivalDays": 3,
        "paymentMethodUsed": "CARD",
    }

    return _serialize({
        "cancellationId": cancellation_id,
        "status": "CANCELLED",
        "refundAmount": refund,
        "currency": r["currency"],
        "cancelledAt": cancelled_at,
    })


@app.get("/api/v1/refunds/{cancellationId}")
def get_refund_status(cancellationId: UUID = Path(...)):
    for refund in DB["refunds"].values():
        if refund["cancellationId"] == cancellationId:
            return _serialize(refund)
    return _err(404, "REFUND-GET-001", "refund record not found for given cancellationId")


# ──────────────── UC_DEMO: намеренные дефекты для демонстрации системы ──────
# Два дефекта:
# 1. SPEC_GAP:          принимает только RUB (спека разрешает RUB/USD/EUR)
# 2. SERVICE_BUG:       суммы кратные 100 → 500 (валидны по схеме, баг сервера)

class _ValidatePaymentRequest(BaseModel):
    amount: Optional[Decimal] = None
    currency: Optional[str] = None


@app.post("/api/v1/demo/payment-validate")
def demo_payment_validate(req: _ValidatePaymentRequest):
    if req.amount is None:
        return _err(400, "DEMO-001", "amount is required")
    if req.amount < 1 or req.amount > 999999:
        return _err(400, "DEMO-001", "amount must be between 1 and 999999")
    if not req.currency:
        return _err(400, "DEMO-002", "currency is required")

    # DEFECT #1 — SPEC_GAP: сервер принимает только RUB, хотя спека разрешает USD/EUR
    # Постановка явно говорит «все стандартные валюты поддерживаются».
    # Diagnosis должен определить это как SPEC_GAP.
    if req.currency != "RUB":
        return _err(400, "DEMO-003",
                    f"unsupported currency: {req.currency}. Only RUB is currently accepted")

    # DEFECT #2 — SERVICE_BUG: суммы кратные 100 вызывают внутреннюю ошибку.
    # Данные полностью валидны по схеме. Diagnosis должен определить SUSPECTED_SERVICE_BUG.
    try:
        amount_int = int(req.amount)
        if amount_int > 0 and amount_int % 100 == 0 and req.amount == Decimal(str(amount_int)):
            return JSONResponse(
                status_code=500,
                content={
                    "errorCode": "DEMO-BUG-500",
                    "message": "internal calculation error: amount causes overflow in pricing engine",
                }
            )
    except Exception:
        pass

    return _serialize({
        "validated": True,
        "amount": req.amount,
        "currency": req.currency,
    })


# ─────────────────── Служебные endpoints (сброс, состояние) ─────────────────

@app.post("/api/v1/mock/reset")
def mock_reset():
    _seed()
    return {"status": "RESET"}


@app.get("/api/v1/mock/state")
def mock_state():
    return _serialize(DB)


@app.get("/api/v1/mock/seed-ids")
def mock_seed_ids():
    return {
        "cars": {
            "COMFORT_cityId77_AVAILABLE":  "11111111-1111-1111-1111-111111111111",
            "SUV_cityId77_MAINTENANCE":    "22222222-2222-2222-2222-222222222222",
            "ECONOMY_cityId36_AVAILABLE":  "33333333-3333-3333-3333-333333333333",
            "PREMIUM_cityId36_RETIRED":    "44444444-4444-4444-4444-444444444444",
        },
        "customers": {
            "ACTIVE":  "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "BLOCKED": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        },
        "payments": {
            "CONFIRMED":  "99999999-9999-9999-9999-999999999999",
            "AUTHORIZED": "77777777-7777-7777-7777-777777777777",
            "DECLINED":   "88888888-8888-8888-8888-888888888888",
        },
        "reservations": {
            "CONFIRMED_seed_for_UC008": "cccccccc-0000-0000-0000-cccccccccccc",
        },
    }
