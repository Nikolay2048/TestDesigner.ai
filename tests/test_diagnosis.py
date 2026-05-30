"""
Тесты для Diagnosis узла.

Layer 1 (constraint check) — детерминированный, тестируется напрямую.
Layer 2 (LLM) — мокируется через patch на create_llm.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.models.diagnosis import DiagnosisCategory
from src.nodes.diagnosis import (
    diagnosis,
    _layer1_check,
    _get_value_from_log,
    _endpoint_summary,
    _LLMDiagnosis,
)


# ─────────────── Вспомогательные данные ────────────────────────────────────

SEARCH_EP = {
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "path": "/api/v1/cars/availability",
    "path_params": [],
    "query_params": [{"name": "cityId", "required": True}],
    "required_fields": [],
    "constraints": {
        "cityId": {"enum": [36, 77]},
        "driverAge": {"minimum": 21, "maximum": 75},
    },
    "response_schemas": {},
}

# Первая (упавшая) попытка с невалидным cityId
ORIGINAL_LOG_INVALID = {
    "step_id": "step_01",
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "url": "http://localhost:8000/api/v1/cars/availability",
    "query_params": {"cityId": "999", "dateFrom": "2099-01-01", "driverAge": "30"},
    "request_body": None,
    "status_code": 400,
    "response": {"errorCode": "CAR-AVAILABILITY-001", "message": "Invalid cityId"},
    "passed": False,
    "stabilize_attempt": 1,
}

# Первая попытка с ВАЛИДНЫМ cityId (данные правильные, сервер отверг)
ORIGINAL_LOG_VALID = {
    **ORIGINAL_LOG_INVALID,
    "query_params": {"cityId": "77", "dateFrom": "2099-01-01", "driverAge": "30"},
    "response": {"errorCode": "CAR-AVAILABILITY-999", "message": "Unknown error"},
}

FIX_CITY_ID = {"name": "cityId", "new_source": "static", "new_value": "77"}

# Вторая (успешная) попытка
SUCCESS_LOG = {
    "step_id": "step_01",
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "url": "http://localhost:8000/api/v1/cars/availability",
    "query_params": {"cityId": "77", "dateFrom": "2099-01-01", "driverAge": "30"},
    "request_body": None,
    "status_code": 200,
    "response": {"items": [{"carId": "car-abc"}]},
    "passed": True,
    "stabilize_attempt": 2,
}

STABILIZED_CARD = {
    "flow_id": "test_flow",
    "name": "test",
    "description": "",
    "requires_flows": [],
    "steps": [],
    "exports": [],
    "teardown_steps": [],
    "is_stabilized": True,
    "stabilization_log": [{
        "step_id": "step_01",
        "attempt": 1,
        "reasoning": "cityId=999 not in [36, 77]",
        "fixes": [FIX_CITY_ID],
    }],
}

EXEC_RESULTS = [{
    "case_id": "test_flow",
    "passed": True,
    "steps_log": [ORIGINAL_LOG_INVALID, SUCCESS_LOG],
    "total_fixes_applied": 1,
}]

ENDPOINTS = [SEARCH_EP]
RAW_SCENARIOS = "Search for cars in cityId 77 or 36."


# ─────────────── _get_value_from_log ───────────────────────────────────────

def test_get_value_from_query_params():
    assert _get_value_from_log(ORIGINAL_LOG_INVALID, "cityId") == "999"


def test_get_value_from_body():
    log = {"query_params": {}, "request_body": {"carId": "car-123"}}
    assert _get_value_from_log(log, "carId") == "car-123"


def test_get_value_returns_none_when_missing():
    assert _get_value_from_log({}, "missing") is None


# ─────────────── _layer1_check ─────────────────────────────────────────────

def test_layer1_enum_violation_returns_test_data_issue():
    category, confidence, evidence = _layer1_check(
        ORIGINAL_LOG_INVALID,
        [FIX_CITY_ID],
        SEARCH_EP["constraints"],
    )
    assert category == DiagnosisCategory.TEST_DATA_ISSUE
    assert confidence >= 0.8
    assert len(evidence) > 0
    assert "999" in evidence[0]


def test_layer1_valid_data_returns_suspected_bug():
    category, confidence, evidence = _layer1_check(
        ORIGINAL_LOG_VALID,
        [FIX_CITY_ID],
        SEARCH_EP["constraints"],
    )
    assert category == DiagnosisCategory.SUSPECTED_SERVICE_BUG
    assert evidence == []


def test_layer1_minimum_violation():
    log = {"query_params": {"driverAge": "15"}, "request_body": None}
    category, confidence, evidence = _layer1_check(
        log,
        [{"name": "driverAge", "new_source": "static", "new_value": "25"}],
        SEARCH_EP["constraints"],
    )
    assert category == DiagnosisCategory.TEST_DATA_ISSUE
    assert "minimum" in evidence[0]


def test_layer1_maximum_violation():
    log = {"query_params": {"driverAge": "99"}, "request_body": None}
    category, confidence, evidence = _layer1_check(
        log,
        [{"name": "driverAge", "new_source": "static", "new_value": "30"}],
        SEARCH_EP["constraints"],
    )
    assert category == DiagnosisCategory.TEST_DATA_ISSUE
    assert "maximum" in evidence[0]


def test_layer1_no_constraints_returns_suspected_bug():
    """Если для поля нет constraints — не можем судить → suspected bug."""
    category, _, _ = _layer1_check(
        {"query_params": {"unknownField": "x"}, "request_body": None},
        [{"name": "unknownField", "new_source": "static", "new_value": "y"}],
        {},
    )
    assert category == DiagnosisCategory.SUSPECTED_SERVICE_BUG


# ─────────────── _endpoint_summary ─────────────────────────────────────────

def test_endpoint_summary_includes_constraints():
    text = _endpoint_summary(SEARCH_EP)
    assert "cityId" in text
    assert "36" in text or "77" in text


# ─────────────── diagnosis node: нет стабилизаций ─────────────────────────

def test_diagnosis_empty_when_no_stabilization():
    state = {
        "exec_results": [{"case_id": "flow", "passed": True, "steps_log": []}],
        "stabilized_card": {"stabilization_log": []},
        "endpoints": ENDPOINTS,
        "raw_scenarios": RAW_SCENARIOS,
    }
    result = diagnosis(state)
    assert result["diagnoses"] == []
    assert "diagnosis" in result["trace"]


def test_diagnosis_empty_when_no_exec_results():
    result = diagnosis({"exec_results": [], "stabilized_card": {}, "endpoints": []})
    assert result["diagnoses"] == []


# ─────────────── diagnosis node: TEST_DATA_ISSUE (Layer 1) ─────────────────

def test_diagnosis_test_data_issue_does_not_call_llm():
    """Layer 1 находит нарушение → LLM не нужен."""
    state = {
        "exec_results": EXEC_RESULTS,
        "stabilized_card": STABILIZED_CARD,
        "endpoints": ENDPOINTS,
        "raw_scenarios": RAW_SCENARIOS,
    }
    with patch("src.nodes.diagnosis.create_llm") as mock_llm:
        result = diagnosis(state)
        mock_llm.assert_not_called()

    assert len(result["diagnoses"]) == 1
    d = result["diagnoses"][0]
    assert d["category"] == DiagnosisCategory.TEST_DATA_ISSUE
    assert d["needs_human"] is False
    assert d["confidence"] >= 0.8


def test_diagnosis_what_changed_is_populated():
    state = {
        "exec_results": EXEC_RESULTS,
        "stabilized_card": STABILIZED_CARD,
        "endpoints": ENDPOINTS,
        "raw_scenarios": RAW_SCENARIOS,
    }
    result = diagnosis(state)
    d = result["diagnoses"][0]
    assert "cityId" in d["what_changed"]
    assert d["what_changed"]["cityId"]["from"] == "999"
    assert d["what_changed"]["cityId"]["to"] == "77"


# ─────────────── diagnosis node: SUSPECTED_SERVICE_BUG (Layer 2) ───────────

def _make_exec_results_with_valid_original():
    """Exec results где оригинальные данные БЫЛИ валидны по спеке."""
    return [{
        "case_id": "test_flow",
        "passed": True,
        "steps_log": [ORIGINAL_LOG_VALID, SUCCESS_LOG],
        "total_fixes_applied": 1,
    }]


@patch("src.nodes.diagnosis.create_llm")
def test_diagnosis_calls_layer2_when_data_was_valid(mock_create_llm):
    """Если Layer 1 не нашёл нарушений → LLM должен быть вызван."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = _LLMDiagnosis(
        category="service_bug",
        confidence=0.8,
        evidence=["Request data matches all constraints", "Server responded with unknown error"],
        reasoning="Data was valid but server rejected",
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    state = {
        "exec_results": _make_exec_results_with_valid_original(),
        "stabilized_card": STABILIZED_CARD,
        "endpoints": ENDPOINTS,
        "raw_scenarios": RAW_SCENARIOS,
    }
    result = diagnosis(state)

    mock_create_llm.assert_called_once()
    assert len(result["diagnoses"]) == 1
    d = result["diagnoses"][0]
    assert d["category"] == DiagnosisCategory.SUSPECTED_SERVICE_BUG
    assert d["needs_human"] is True  # жёсткое правило: service_bug → needs_human


@patch("src.nodes.diagnosis.create_llm")
def test_diagnosis_needs_human_when_confidence_low(mock_create_llm):
    """LLM возвращает низкий confidence → needs_human=True."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = _LLMDiagnosis(
        category="spec_gap",
        confidence=0.4,  # низкий
        evidence=["Maybe the scenario says something"],
        reasoning="Not sure",
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    state = {
        "exec_results": _make_exec_results_with_valid_original(),
        "stabilized_card": STABILIZED_CARD,
        "endpoints": ENDPOINTS,
        "raw_scenarios": RAW_SCENARIOS,
    }
    result = diagnosis(state)

    d = result["diagnoses"][0]
    assert d["needs_human"] is True  # confidence < 0.6 → needs_human


@patch("src.nodes.diagnosis.create_llm")
def test_diagnosis_needs_human_when_evidence_empty(mock_create_llm):
    """LLM возвращает пустой evidence → needs_human=True, confidence снижается."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = _LLMDiagnosis(
        category="spec_gap",
        confidence=0.85,  # высокий, но evidence пустой
        evidence=[],
        reasoning="No evidence",
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    state = {
        "exec_results": _make_exec_results_with_valid_original(),
        "stabilized_card": STABILIZED_CARD,
        "endpoints": ENDPOINTS,
        "raw_scenarios": RAW_SCENARIOS,
    }
    result = diagnosis(state)

    d = result["diagnoses"][0]
    assert d["needs_human"] is True
    assert d["confidence"] <= 0.4  # понижен


@patch("src.nodes.diagnosis.create_llm")
def test_diagnosis_spec_gap_does_not_need_human_if_confident(mock_create_llm):
    """SPEC_GAP с высоким confidence и evidence → needs_human=False."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = _LLMDiagnosis(
        category="spec_gap",
        confidence=0.85,
        evidence=["Scenario says: only city 77 allowed for COMFORT class"],
        reasoning="Spec is incomplete",
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    state = {
        "exec_results": _make_exec_results_with_valid_original(),
        "stabilized_card": STABILIZED_CARD,
        "endpoints": ENDPOINTS,
        "raw_scenarios": RAW_SCENARIOS,
    }
    result = diagnosis(state)

    d = result["diagnoses"][0]
    assert d["category"] == DiagnosisCategory.SPEC_GAP
    assert d["needs_human"] is False
    assert d["confidence"] >= 0.6
