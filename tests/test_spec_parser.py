"""Тесты для Spec Parser (детерминированный код — покрывать обязательно)."""

from pathlib import Path

import pytest

from src.spec_parser import parse_openapi_file, parse_openapi_files

DATA = Path(__file__).parent.parent / "data" / "scenarios" / "openapi_spec"


# ──────────────────────────── UC_001: поиск авто ────────────────────────────

def test_uc001_returns_one_endpoint():
    eps = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")
    assert len(eps) == 1


def test_uc001_operation_meta():
    ep = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")[0]
    assert ep.operation_id == "searchAvailableCars"
    assert ep.method == "GET"
    assert ep.path == "/api/v1/cars/availability"


def test_uc001_no_request_body():
    ep = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")[0]
    assert ep.request_schema is None
    assert ep.required_fields == []


def test_uc001_query_params_present():
    ep = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")[0]
    names = {p["name"] for p in ep.query_params}
    assert names == {"cityId", "dateFrom", "dateTo", "driverAge", "carClass"}


def test_uc001_no_path_params():
    ep = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")[0]
    assert ep.path_params == []


def test_uc001_city_id_enum_constraint():
    ep = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")[0]
    assert ep.constraints["cityId"]["enum"] == [36, 77]


def test_uc001_driver_age_range_constraint():
    ep = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")[0]
    assert ep.constraints["driverAge"]["minimum"] == 21
    assert ep.constraints["driverAge"]["maximum"] == 75


def test_uc001_car_class_enum_constraint():
    ep = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")[0]
    assert set(ep.constraints["carClass"]["enum"]) == {
        "ECONOMY", "COMFORT", "BUSINESS", "SUV", "PREMIUM"
    }


def test_uc001_response_schemas_present():
    ep = parse_openapi_file(DATA / "UC_001_SearchAvailableCars.yaml")[0]
    assert "200" in ep.response_schemas
    assert "400" in ep.response_schemas


# ──────────────────────────── UC_002: черновик брони ────────────────────────

def test_uc002_operation_meta():
    ep = parse_openapi_file(DATA / "UC_002_CreateReservationDraft.yaml")[0]
    assert ep.operation_id == "createReservationDraft"
    assert ep.method == "POST"
    assert ep.path == "/api/v1/reservations/drafts"


def test_uc002_required_fields():
    ep = parse_openapi_file(DATA / "UC_002_CreateReservationDraft.yaml")[0]
    assert set(ep.required_fields) == {
        "carId", "cityId", "customerId", "dateFrom", "dateTo", "tariffCode"
    }


def test_uc002_tariff_enum_constraint():
    ep = parse_openapi_file(DATA / "UC_002_CreateReservationDraft.yaml")[0]
    assert set(ep.constraints["tariffCode"]["enum"]) == {"BASE", "FULL_INSURANCE", "PREMIUM"}


def test_uc002_promo_code_max_length_constraint():
    ep = parse_openapi_file(DATA / "UC_002_CreateReservationDraft.yaml")[0]
    assert ep.constraints["promoCode"]["maxLength"] == 50


def test_uc002_response_schemas():
    ep = parse_openapi_file(DATA / "UC_002_CreateReservationDraft.yaml")[0]
    assert "201" in ep.response_schemas
    assert "400" in ep.response_schemas
    assert "409" in ep.response_schemas


# ──────────────────────────── UC_003: подтверждение ────────────────────────

def test_uc003_operation_meta():
    ep = parse_openapi_file(DATA / "UC_003_ConfirmReservation.yaml")[0]
    assert ep.operation_id == "confirmReservation"
    assert ep.method == "POST"
    assert ep.path == "/api/v1/reservations/{reservationDraftId}/confirm"


def test_uc003_path_param():
    ep = parse_openapi_file(DATA / "UC_003_ConfirmReservation.yaml")[0]
    assert len(ep.path_params) == 1
    assert ep.path_params[0]["name"] == "reservationDraftId"


def test_uc003_required_fields():
    ep = parse_openapi_file(DATA / "UC_003_ConfirmReservation.yaml")[0]
    assert set(ep.required_fields) == {"paymentId", "paymentAmount", "currency"}


def test_uc003_currency_enum_constraint():
    ep = parse_openapi_file(DATA / "UC_003_ConfirmReservation.yaml")[0]
    assert set(ep.constraints["currency"]["enum"]) == {"RUB", "USD", "EUR"}


# ─────────────────────── parse_openapi_files (несколько файлов) ────────────

def test_parse_all_three_files():
    eps = parse_openapi_files([
        DATA / "UC_001_SearchAvailableCars.yaml",
        DATA / "UC_002_CreateReservationDraft.yaml",
        DATA / "UC_003_ConfirmReservation.yaml",
    ])
    assert len(eps) == 3
    ids = {ep.operation_id for ep in eps}
    assert ids == {"searchAvailableCars", "createReservationDraft", "confirmReservation"}
