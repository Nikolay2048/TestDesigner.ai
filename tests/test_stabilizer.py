"""
Тесты для stabilizer agent и обновлённого executor_stabilize.

LLM мокируется — проверяем контракт, а не качество модели.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.models.flow import ScenarioStep, VariableBinding, VarSource
from src.nodes.stabilizer import (
    stabilize_step,
    _apply_fix,
    _spec_with_constraints,
    _format_inputs,
    InputFix,
    StepFix,
)
from src.nodes.executor_stabilize import executor_stabilize


# ─────────────── Вспомогательные данные ────────────────────────────────────

SEARCH_EP = {
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "path": "/api/v1/cars/availability",
    "path_params": [],
    "query_params": [
        {"name": "cityId", "required": True},
        {"name": "dateFrom", "required": True},
        {"name": "driverAge", "required": True},
        {"name": "carClass", "required": False},
    ],
    "required_fields": [],
    "constraints": {
        "cityId": {"enum": [36, 77]},
        "driverAge": {"minimum": 21, "maximum": 75},
        "carClass": {"enum": ["ECONOMY", "COMFORT"]},
    },
    "response_schemas": {},
}

SEARCH_STEP = ScenarioStep(
    step_id="step_01",
    operation_id="searchAvailableCars",
    inputs=[
        VariableBinding(name="cityId", source=VarSource.STATIC, value="999",
                        target_location="query.cityId"),
        VariableBinding(name="dateFrom", source=VarSource.GENERATED,
                        generator="future_datetime", target_location="query.dateFrom"),
        VariableBinding(name="driverAge", source=VarSource.STATIC, value="30",
                        target_location="query.driverAge"),
    ],
    produces=["$.items[0].carId"],
)

FAILED_LOG = {
    "step_id": "step_01",
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "url": "http://localhost:8000/api/v1/cars/availability",
    "query_params": {"cityId": "999", "dateFrom": "2099-01-01T10:00:00", "driverAge": "30"},
    "request_body": None,
    "status_code": 400,
    "response": {"errorCode": "CAR-AVAILABILITY-001", "message": "Invalid cityId"},
    "passed": False,
}

VALID_FIX = StepFix(
    reasoning="cityId=999 is not in allowed values [36, 77]",
    fixes=[InputFix(name="cityId", new_source="static", new_value="77",
                    explanation="77 is a valid cityId")],
    unfixable=False,
)


# ─────────────── _spec_with_constraints ────────────────────────────────────

def test_spec_with_constraints_shows_enum():
    text = _spec_with_constraints(SEARCH_EP)
    assert "36" in text or "77" in text
    assert "cityId" in text


def test_spec_with_constraints_shows_range():
    text = _spec_with_constraints(SEARCH_EP)
    assert "21" in text  # min driverAge
    assert "75" in text  # max driverAge


# ─────────────── _format_inputs ────────────────────────────────────────────

def test_format_inputs_static():
    text = _format_inputs(SEARCH_STEP)
    assert "cityId" in text
    assert "999" in text
    assert "static" in text


def test_format_inputs_marks_from_step_as_do_not_change():
    step = ScenarioStep(
        step_id="step_02",
        operation_id="createReservationDraft",
        inputs=[
            VariableBinding(name="carId", source=VarSource.FROM_STEP,
                            source_ref="step_01", source_field="$.items[0].carId",
                            target_location="body.carId"),
        ],
        produces=[],
    )
    text = _format_inputs(step)
    assert "DO NOT CHANGE" in text


# ─────────────── _apply_fix ────────────────────────────────────────────────

def test_apply_fix_changes_target_field():
    fixed = _apply_fix(SEARCH_STEP, VALID_FIX)
    city = next(i for i in fixed.inputs if i.name == "cityId")
    assert city.value == "77"
    assert city.source == VarSource.STATIC


def test_apply_fix_leaves_other_fields_unchanged():
    fixed = _apply_fix(SEARCH_STEP, VALID_FIX)
    date = next(i for i in fixed.inputs if i.name == "dateFrom")
    assert date.source == VarSource.GENERATED
    assert date.generator == "future_datetime"


def test_apply_fix_does_not_mutate_original():
    _apply_fix(SEARCH_STEP, VALID_FIX)
    original_city = next(i for i in SEARCH_STEP.inputs if i.name == "cityId")
    assert original_city.value == "999"  # unchanged


# ─────────────── stabilize_step (LLM мок) ──────────────────────────────────

@patch("src.nodes.stabilizer.create_llm")
def test_stabilize_step_returns_fixed_step(mock_create_llm):
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = VALID_FIX
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    fixed_step, fix = stabilize_step(SEARCH_STEP, SEARCH_EP, FAILED_LOG)

    assert fixed_step is not None
    city = next(i for i in fixed_step.inputs if i.name == "cityId")
    assert city.value == "77"


@patch("src.nodes.stabilizer.create_llm")
def test_stabilize_step_unfixable(mock_create_llm):
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = StepFix(
        reasoning="Server returned 500, likely a bug",
        fixes=[],
        unfixable=True,
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    fixed_step, fix = stabilize_step(SEARCH_STEP, SEARCH_EP, FAILED_LOG)

    assert fixed_step is None
    assert fix is not None
    assert fix.unfixable is True


@patch("src.nodes.stabilizer.create_llm")
def test_stabilize_step_ignores_fix_for_unknown_field(mock_create_llm):
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = StepFix(
        reasoning="wrong field",
        fixes=[InputFix(name="nonExistentField", new_source="static",
                        new_value="abc", explanation="test")],
        unfixable=False,
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    fixed_step, fix = stabilize_step(SEARCH_STEP, SEARCH_EP, FAILED_LOG)
    assert fixed_step is None  # fix references unknown field → rejected


@patch("src.nodes.stabilizer.create_llm")
def test_stabilize_step_does_not_change_from_step_fields(mock_create_llm):
    """Стабилизатор не должен трогать from_step/env поля."""
    step_with_from = ScenarioStep(
        step_id="step_02",
        operation_id="createReservationDraft",
        inputs=[
            VariableBinding(name="carId", source=VarSource.FROM_STEP,
                            source_ref="step_01", source_field="$.items[0].carId",
                            target_location="body.carId"),
            VariableBinding(name="cityId", source=VarSource.STATIC, value="999",
                            target_location="body.cityId"),
        ],
        produces=[],
    )
    ep = {**SEARCH_EP, "operation_id": "createReservationDraft", "method": "POST",
          "path": "/api/v1/reservations/drafts", "query_params": [],
          "required_fields": ["carId", "cityId"]}

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    # LLM tries to change from_step field (violation)
    mock_structured.return_value = StepFix(
        reasoning="both fields are wrong",
        fixes=[
            InputFix(name="carId", new_source="static", new_value="fake-car",
                     explanation="bad"),
            InputFix(name="cityId", new_source="static", new_value="77",
                     explanation="valid"),
        ],
        unfixable=False,
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    fixed_step, fix = stabilize_step(step_with_from, ep, FAILED_LOG)

    # carId fix should be dropped, cityId fix should be applied
    assert fixed_step is not None
    car = next(i for i in fixed_step.inputs if i.name == "carId")
    city = next(i for i in fixed_step.inputs if i.name == "cityId")
    assert car.source == VarSource.FROM_STEP  # unchanged
    assert city.value == "77"  # fixed


@patch("src.nodes.stabilizer.create_llm")
def test_stabilize_step_returns_none_on_llm_error(mock_create_llm):
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.side_effect = RuntimeError("LLM timeout")
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    fixed_step, fix = stabilize_step(SEARCH_STEP, SEARCH_EP, FAILED_LOG)
    assert fixed_step is None
    assert fix is None


# ─────────── executor_stabilize с интеграцией стабилизатора ────────────────

def _make_http_mock(status: int, json_data: dict):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    resp.content = b"..."
    return resp


SAMPLE_FLOW_CARD = {
    "flow_id": "test_flow",
    "name": "Test",
    "description": "",
    "requires_flows": [],
    "steps": [{
        "step_id": "step_01",
        "operation_id": "searchAvailableCars",
        "inputs": [
            {"name": "cityId", "source": "static", "value": "999",
             "target_location": "query.cityId"},
            {"name": "dateFrom", "source": "generated", "generator": "future_datetime",
             "target_location": "query.dateFrom"},
            {"name": "driverAge", "source": "static", "value": "30",
             "target_location": "query.driverAge"},
        ],
        "produces": ["$.items[0].carId"],
        "depends_on": [],
    }],
    "exports": [],
    "teardown_steps": [],
    "is_stabilized": False,
    "stabilization_log": [],
}

SAMPLE_ENDPOINTS = [{
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "path": "/api/v1/cars/availability",
    "path_params": [],
    "query_params": [{"name": "cityId", "required": True}],
    "required_fields": [],
    "constraints": {"cityId": {"enum": [36, 77]}},
    "request_schema": None,
    "response_schemas": {},
}]


@patch("src.nodes.stabilizer.create_llm")
@patch("src.executor.http.request")
def test_executor_retries_after_stabilizer_fix(mock_req, mock_create_llm):
    """Первая попытка → 400, стабилизатор фиксит cityId, вторая → 200."""
    mock_req.side_effect = [
        _make_http_mock(400, {"errorCode": "CAR-AVAILABILITY-001", "message": "Invalid cityId"}),
        _make_http_mock(200, {"items": [{"carId": "car-abc"}]}),
    ]

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = StepFix(
        reasoning="cityId=999 not in [36,77]",
        fixes=[InputFix(name="cityId", new_source="static", new_value="77",
                        explanation="valid cityId")],
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    result = executor_stabilize({"flow_card": SAMPLE_FLOW_CARD, "endpoints": SAMPLE_ENDPOINTS})

    assert result["stabilized_card"]["is_stabilized"] is True
    assert result["exec_results"][0]["passed"] is True
    assert result["exec_results"][0]["total_fixes_applied"] == 1
    assert len(result["stabilized_card"]["stabilization_log"]) == 1


@patch("src.nodes.stabilizer.create_llm")
@patch("src.executor.http.request")
def test_executor_stops_when_stabilizer_unfixable(mock_req, mock_create_llm):
    """Стабилизатор говорит unfixable → executor останавливается."""
    mock_req.return_value = _make_http_mock(500, {"error": "internal server error"})

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.return_value = StepFix(
        reasoning="Server 500, suspected bug",
        fixes=[],
        unfixable=True,
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    result = executor_stabilize({"flow_card": SAMPLE_FLOW_CARD, "endpoints": SAMPLE_ENDPOINTS})

    assert result["stabilized_card"]["is_stabilized"] is False
    assert result["exec_results"][0]["passed"] is False


@patch("src.nodes.stabilizer.create_llm")
@patch("src.executor.http.request")
def test_executor_respects_max_step_attempts(mock_req, mock_create_llm):
    """После MAX_STEP_ATTEMPTS неудач — останавливаемся."""
    mock_req.return_value = _make_http_mock(400, {"error": "bad"})

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    # Стабилизатор всегда предлагает фикс (но он не помогает — сервер всё равно 400)
    mock_structured.return_value = StepFix(
        reasoning="Try another cityId",
        fixes=[InputFix(name="cityId", new_source="static", new_value="36",
                        explanation="try 36")],
    )
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    result = executor_stabilize({"flow_card": SAMPLE_FLOW_CARD, "endpoints": SAMPLE_ENDPOINTS})

    assert result["stabilized_card"]["is_stabilized"] is False
    # MAX_STEP_ATTEMPTS=3: 1 оригинал + 2 стабилизации
    log = result["exec_results"][0]["steps_log"]
    assert len(log) == 3
