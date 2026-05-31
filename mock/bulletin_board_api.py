"""FastAPI mock-сервер доски объявлений.

Реализует UC_101 / UC_102 / UC_103.
Endpoints соответствуют YAML-спекам в data/scenarios/openapi_spec/.

Seed-данные:
  user ACTIVE seller → aaaa0001-aaaa-aaaa-aaaa-aaaaaaaaaaaa
  user ACTIVE buyer  → aaaa0002-aaaa-aaaa-aaaa-aaaaaaaaaaaa
  user BANNED        → aaaa0003-aaaa-aaaa-aaaa-aaaaaaaaaaaa

  Seed-токены (для env_vars):
    token-seller-aaa00001  → aaaa0001-... (seller)
    token-buyer-aaa00002   → aaaa0002-... (buyer)

  ad PUBLISHED ELECTRONICS Moscow 15000 RUB → bbbb0001-bbbb-bbbb-bbbb-bbbbbbbbbbbb
  ad DRAFT     FURNITURE   Samara  3000 RUB → bbbb0002-bbbb-bbbb-bbbb-bbbbbbbbbbbb
  ad CLOSED    CLOTHING    Moscow   500 RUB → bbbb0003-bbbb-bbbb-bbbb-bbbbbbbbbbbb
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Bulletin Board Mock Server", version="1.0.0")

AD_CATEGORIES = {"ELECTRONICS", "CLOTHING", "FURNITURE", "TRANSPORT", "SERVICES", "OTHER"}
CURRENCIES = {"RUB", "USD", "EUR"}
MIN_PASSWORD_LEN = 6
MAX_MESSAGE_LEN = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"errorCode": code, "message": message})


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
    seller_id = UUID("aaaa0001-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    buyer_id  = UUID("aaaa0002-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    banned_id = UUID("aaaa0003-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    ad_pub    = UUID("bbbb0001-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    ad_draft  = UUID("bbbb0002-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    ad_closed = UUID("bbbb0003-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    now = _now()

    DB.clear()
    DB.update({
        "users": {
            seller_id: {
                "userId": seller_id, "email": "seller@example.com",
                "password": "secret123", "name": "Ivan Seller", "status": "ACTIVE",
            },
            buyer_id: {
                "userId": buyer_id, "email": "buyer@example.com",
                "password": "buyer456", "name": "Anna Buyer", "status": "ACTIVE",
            },
            banned_id: {
                "userId": banned_id, "email": "banned@example.com",
                "password": "banned789", "name": "Banned User", "status": "BANNED",
            },
        },
        "tokens": {
            "token-seller-aaa00001": seller_id,
            "token-buyer-aaa00002":  buyer_id,
        },
        "ads": {
            ad_pub: {
                "adId": ad_pub, "authorId": seller_id,
                "title": "iPhone 14 Pro отличное состояние",
                "description": "Продаю iPhone 14 Pro 256 GB Space Black. Полный комплект.",
                "price": Decimal("15000"), "currency": "RUB",
                "category": "ELECTRONICS", "city": "Moscow",
                "status": "PUBLISHED", "viewCount": 0,
                "createdAt": now, "publishedAt": now,
            },
            ad_draft: {
                "adId": ad_draft, "authorId": seller_id,
                "title": "Диван угловой",
                "description": "Угловой диван в хорошем состоянии, самовывоз.",
                "price": Decimal("3000"), "currency": "RUB",
                "category": "FURNITURE", "city": "Samara",
                "status": "DRAFT", "viewCount": 0,
                "createdAt": now, "publishedAt": None,
            },
            ad_closed: {
                "adId": ad_closed, "authorId": seller_id,
                "title": "Куртка зимняя",
                "description": "Зимняя куртка, размер M, б/у.",
                "price": Decimal("500"), "currency": "RUB",
                "category": "CLOTHING", "city": "Moscow",
                "status": "CLOSED", "viewCount": 3,
                "createdAt": now, "publishedAt": now,
            },
        },
        "responses": {},
    })


_seed()


# ─────────────────────── Auth helper ────────────────────────────────────────

def _auth(authorization: Optional[str]) -> tuple[dict | None, JSONResponse | None]:
    """Возвращает (user, None) при успехе или (None, err_response) при ошибке."""
    if not authorization or not authorization.startswith("Bearer "):
        return None, _err(401, "BB-AD-010", "Authorization header missing or invalid")
    token = authorization[7:].strip()
    uid = DB["tokens"].get(token)
    if not uid:
        return None, _err(401, "BB-AD-010", "token is invalid or expired")
    user = DB["users"].get(uid)
    if not user:
        return None, _err(401, "BB-AD-010", "token is invalid or expired")
    return user, None


# ──────────────── UC_101: регистрация и создание объявления ─────────────────

class RegisterUserRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    name: Optional[str] = None


@app.post("/api/bb/v1/users/register", status_code=201)
def register_user(req: RegisterUserRequest):
    if not req.email or "@" not in req.email:
        return _err(400, "BB-USER-001", "email is required and must be a valid email address")
    if not req.password or len(req.password) < MIN_PASSWORD_LEN:
        return _err(400, "BB-USER-002", f"password must be at least {MIN_PASSWORD_LEN} characters")
    if not req.name or not req.name.strip():
        return _err(400, "BB-USER-004", "name is required")

    email_lower = req.email.lower()
    for u in DB["users"].values():
        if u["email"].lower() == email_lower:
            return _err(409, "BB-USER-003", "email is already registered")

    user_id = uuid4()
    DB["users"][user_id] = {
        "userId": user_id, "email": req.email,
        "password": req.password, "name": req.name, "status": "ACTIVE",
    }
    return _serialize({"userId": user_id, "email": req.email, "status": "ACTIVE"})


class LoginUserRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None


@app.post("/api/bb/v1/users/login")
def login_user(req: LoginUserRequest):
    if not req.email or not req.password:
        return _err(400, "BB-AUTH-001", "email and password are required")

    email_lower = req.email.lower()
    user = next((u for u in DB["users"].values() if u["email"].lower() == email_lower), None)

    if not user or user["password"] != req.password:
        return _err(401, "BB-AUTH-002", "invalid email or password")
    if user["status"] == "BANNED":
        return _err(403, "BB-AUTH-003", "user is banned")

    token = str(uuid4())
    DB["tokens"][token] = user["userId"]
    return _serialize({"userId": user["userId"], "token": token})


class CreateAdRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[Decimal] = None
    currency: Optional[str] = None
    category: Optional[str] = None
    city: Optional[str] = None


@app.post("/api/bb/v1/ads", status_code=201)
def create_ad(req: CreateAdRequest, authorization: Optional[str] = Header(None)):
    user, err = _auth(authorization)
    if err:
        return err

    if not req.title or not req.title.strip():
        return _err(400, "BB-AD-001", "title is required")
    if len(req.title) > 200:
        return _err(400, "BB-AD-001", "title must not exceed 200 characters")
    if req.description is None or not req.description.strip():
        return _err(400, "BB-AD-006", "description is required")
    if req.price is None or req.price <= 0:
        return _err(400, "BB-AD-002", "price must be a positive number")
    if not req.category or req.category not in AD_CATEGORIES:
        return _err(400, "BB-AD-003", f"category must be one of {sorted(AD_CATEGORIES)}")
    if not req.currency or req.currency not in CURRENCIES:
        return _err(400, "BB-AD-004", f"currency must be one of {sorted(CURRENCIES)}")
    if not req.city or not req.city.strip():
        return _err(400, "BB-AD-005", "city is required")

    ad_id = uuid4()
    now = _now()
    DB["ads"][ad_id] = {
        "adId": ad_id, "authorId": user["userId"],
        "title": req.title.strip(),
        "description": req.description or "",
        "price": req.price, "currency": req.currency,
        "category": req.category, "city": req.city.strip(),
        "status": "DRAFT", "viewCount": 0,
        "createdAt": now, "publishedAt": None,
    }
    return _serialize({
        "adId": ad_id, "status": "DRAFT",
        "authorId": user["userId"], "createdAt": now,
    })


@app.post("/api/bb/v1/ads/{adId}/publish")
def publish_ad(adId: UUID = Path(...), authorization: Optional[str] = Header(None)):
    user, err = _auth(authorization)
    if err:
        return err

    ad = DB["ads"].get(adId)
    if not ad:
        return _err(404, "BB-AD-020", "ad not found")
    if ad["authorId"] != user["userId"]:
        return _err(403, "BB-AD-021", "only the ad author can publish this ad")
    if ad["status"] != "DRAFT":
        return _err(409, "BB-AD-022", "only DRAFT ads can be published")

    published_at = _now()
    ad["status"] = "PUBLISHED"
    ad["publishedAt"] = published_at
    return _serialize({"adId": adId, "status": "PUBLISHED", "publishedAt": published_at})


# ──────────────── UC_102: поиск и просмотр объявлений ──────────────────────

@app.get("/api/bb/v1/ads")
def search_ads(
    category: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    priceMin: Optional[float] = Query(None),
    priceMax: Optional[float] = Query(None),
    page: int = Query(default=1),
    pageSize: int = Query(default=20),
):
    if category and category not in AD_CATEGORIES:
        return _err(400, "BB-SEARCH-002", f"category must be one of {sorted(AD_CATEGORIES)}")
    if pageSize > 100:
        return _err(400, "BB-SEARCH-003", "pageSize must not exceed 100")
    if priceMin is not None and priceMax is not None and priceMin > priceMax:
        return _err(400, "BB-SEARCH-001", "priceMin must not exceed priceMax")
    if page < 1:
        return _err(400, "BB-SEARCH-004", "page must be at least 1")

    items = [
        ad for ad in DB["ads"].values()
        if ad["status"] == "PUBLISHED"
        and (not category or ad["category"] == category)
        and (not city or ad["city"].lower() == city.lower())
        and (priceMin is None or float(ad["price"]) >= priceMin)
        and (priceMax is None or float(ad["price"]) <= priceMax)
    ]

    total = len(items)
    start = (page - 1) * pageSize
    page_items = items[start: start + pageSize]

    return _serialize({
        "items": [
            {
                "adId": ad["adId"], "title": ad["title"],
                "price": ad["price"], "currency": ad["currency"],
                "category": ad["category"], "city": ad["city"],
                "status": ad["status"], "viewCount": ad["viewCount"],
            }
            for ad in page_items
        ],
        "total": total,
        "page": page,
        "pageSize": pageSize,
    })


@app.get("/api/bb/v1/ads/{adId}")
def get_ad_details(adId: UUID = Path(...)):
    ad = DB["ads"].get(adId)
    if not ad:
        return _err(404, "BB-AD-030", "ad not found")

    ad["viewCount"] += 1
    return _serialize(ad)


# ──────────────── UC_103: отклики на объявление ──────────────────────────────

class CreateResponseRequest(BaseModel):
    message: Optional[str] = None


@app.post("/api/bb/v1/ads/{adId}/responses", status_code=201)
def create_response(
    adId: UUID = Path(...),
    req: CreateResponseRequest = ...,
    authorization: Optional[str] = Header(None),
):
    user, err = _auth(authorization)
    if err:
        return err

    ad = DB["ads"].get(adId)
    if not ad:
        return _err(404, "BB-AD-030", "ad not found")
    if ad["status"] != "PUBLISHED":
        return _err(409, "BB-RESP-004", "only PUBLISHED ads accept responses")
    if ad["authorId"] == user["userId"]:
        return _err(403, "BB-RESP-003", "ad author cannot respond to own ad")
    if not req.message or not req.message.strip():
        return _err(400, "BB-RESP-001", "message is required")
    if len(req.message) > MAX_MESSAGE_LEN:
        return _err(400, "BB-RESP-002", f"message must not exceed {MAX_MESSAGE_LEN} characters")

    resp_id = uuid4()
    now = _now()
    DB["responses"][resp_id] = {
        "responseId": resp_id, "adId": adId,
        "buyerId": user["userId"], "message": req.message,
        "status": "NEW", "createdAt": now,
    }
    return _serialize({
        "responseId": resp_id, "adId": adId,
        "buyerId": user["userId"], "message": req.message,
        "status": "NEW", "createdAt": now,
    })


@app.get("/api/bb/v1/ads/{adId}/responses")
def get_ad_responses(
    adId: UUID = Path(...),
    authorization: Optional[str] = Header(None),
):
    user, err = _auth(authorization)
    if err:
        return err

    ad = DB["ads"].get(adId)
    if not ad:
        return _err(404, "BB-AD-030", "ad not found")
    if ad["authorId"] != user["userId"]:
        return _err(403, "BB-RESP-010", "only the ad author can view responses")

    items = [r for r in DB["responses"].values() if r["adId"] == adId]
    return _serialize({
        "items": [
            {
                "responseId": r["responseId"], "buyerId": r["buyerId"],
                "message": r["message"], "status": r["status"],
                "createdAt": r["createdAt"],
            }
            for r in items
        ]
    })


class MarkResponseReadRequest(BaseModel):
    status: Optional[str] = None


@app.patch("/api/bb/v1/responses/{responseId}")
def mark_response_read(
    responseId: UUID = Path(...),
    req: MarkResponseReadRequest = ...,
    authorization: Optional[str] = Header(None),
):
    user, err = _auth(authorization)
    if err:
        return err

    resp = DB["responses"].get(responseId)
    if not resp:
        return _err(404, "BB-RESP-020", "response not found")

    ad = DB["ads"].get(resp["adId"])
    if not ad or ad["authorId"] != user["userId"]:
        return _err(403, "BB-RESP-011", "only the ad author can mark responses as read")
    if resp["status"] == "READ":
        return _err(409, "BB-RESP-021", "response is already read")

    resp["status"] = "READ"
    return _serialize({"responseId": responseId, "status": "READ"})


# ─────────────────── Служебные endpoints ────────────────────────────────────

@app.post("/api/bb/v1/mock/reset")
@app.post("/api/v1/mock/reset")   # alias — executor_stabilize uses this path
def mock_reset():
    _seed()
    return {"status": "RESET"}


@app.get("/api/bb/v1/mock/state")
def mock_state():
    return _serialize(DB)


@app.get("/api/bb/v1/mock/seed-ids")
def mock_seed_ids():
    return {
        "users": {
            "ACTIVE_seller": "aaaa0001-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "ACTIVE_buyer":  "aaaa0002-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "BANNED":        "aaaa0003-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        },
        "tokens": {
            "seller": "token-seller-aaa00001",
            "buyer":  "token-buyer-aaa00002",
        },
        "ads": {
            "PUBLISHED_ELECTRONICS_Moscow": "bbbb0001-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "DRAFT_FURNITURE_Samara":       "bbbb0002-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "CLOSED_CLOTHING_Moscow":       "bbbb0003-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        },
    }
