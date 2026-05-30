"""
Тесты для Scenario Analyst.

LLM-узлы мокируются — проверяем контракт (вход/выход), а не качество LLM.
Архитектура: два отдельных LLM-вызова (Phase 1: выбор операций, Phase 2: биндинг).
"""

from unittest.mock import MagicMock, patch

import pytest

from src.models.flow import FlowCard, ScenarioStep, VariableBinding, VarSource
from src.nodes.scenario_analyst import (
    scenario_analyst,
    _endpoint_spec_text,
    _previous_context_text,
    OperationSelection,
    StepInputs,
)


# ─────────────────── Вспомогательные данные ────────────────────────────────

SAMPLE_ENDPOINTS = [
    {
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
        "constraints": {},
        "request_schema": None,
        "response_schemas": {"200": {"properties": {"items": {}, "total": {}}}},
    }
]

SAMPLE_SCENARIO = "Find available cars in city 77 for 2 days."

SAMPLE_SELECTION = OperationSelection(ordered_operation_ids=["searchAvailableCars"])

SAMPLE_STEP_INPUTS = StepInputs(
    inputs=[
        VariableBinding(name="cityId", source=VarSource.STATIC, value="77",
                        target_location="query.cityId"),
        VariableBinding(name="dateFrom", source=VarSource.GENERATED,
                        generator="future_datetime", target_location="query.dateFrom"),
        VariableBinding(name="driverAge", source=VarSource.STATIC, value="30",
                        target_location="query.driverAge"),
    ],
    produces=["$.items[0].carId"],
)


# ─────────────── Тесты _endpoint_spec_text ─────────────────────────────────

def test_endpoint_spec_text_includes_method_and_path():
    text = _endpoint_spec_text(SAMPLE_ENDPOINTS[0])
    assert "GET" in text
    assert "/api/v1/cars/availability" in text


def test_endpoint_spec_text_lists_required_params():
    text = _endpoint_spec_text(SAMPLE_ENDPOINTS[0])
    assert "cityId" in text
    assert "required" in text.lower()


def test_endpoint_spec_text_lists_optional_params():
    text = _endpoint_spec_text(SAMPLE_ENDPOINTS[0])
    assert "carClass" in text
    assert "optional" in text.lower()


def test_endpoint_spec_text_lists_response_fields():
    text = _endpoint_spec_text(SAMPLE_ENDPOINTS[0])
    assert "items" in text


# ─────────────── Тесты _previous_context_text ──────────────────────────────

def test_previous_context_empty_for_first_step():
    text = _previous_context_text([], {})
    assert "first step" in text.lower() or "none" in text.lower()


def test_previous_context_lists_completed_steps():
    step = ScenarioStep(
        step_id="step_01",
        operation_id="searchAvailableCars",
        inputs=[],
        produces=["$.items[0].carId"],
    )
    ep_map = {"searchAvailableCars": SAMPLE_ENDPOINTS[0]}
    text = _previous_context_text([step], ep_map)
    assert "step_01" in text
    assert "searchAvailableCars" in text


# ── Тест узла: успешный путь (два LLM-вызова замокированы) ─────────────────

@patch("src.nodes.scenario_analyst.create_llm")
def test_node_returns_flow_card_on_success(mock_create_llm):
    """
    Два with_structured_output вызываются последовательно.
    LCEL вызывает mock через __call__, поэтому side_effect задаётся
    на объект, возвращаемый with_structured_output.

    mock_llm.with_structured_output вызывается дважды:
      call 1 → select_structured (Phase 1)
      call 2 → bind_structured  (Phase 2, step_01)

    Каждый из них используется в chain = prompt | structured.
    LCEL оборачивает не-Runnable в RunnableLambda и вызывает через __call__.
    Поэтому return_value задаётся на сам mock_structured, а не на .invoke().
    """
    mock_llm = MagicMock()

    select_structured = MagicMock()
    select_structured.return_value = SAMPLE_SELECTION

    bind_structured = MagicMock()
    bind_structured.return_value = SAMPLE_STEP_INPUTS

    mock_llm.with_structured_output.side_effect = [select_structured, bind_structured]
    mock_create_llm.return_value = mock_llm

    state = {"endpoints": SAMPLE_ENDPOINTS, "raw_scenarios": SAMPLE_SCENARIO}
    result = scenario_analyst(state)

    assert "flow_card" in result
    assert result["flow_card"]["steps"][0]["operation_id"] == "searchAvailableCars"
    assert "scenario_analyst" in result["trace"]


@patch("src.nodes.scenario_analyst.create_llm")
def test_node_returns_empty_on_empty_state(mock_create_llm):
    result = scenario_analyst({"endpoints": [], "raw_scenarios": ""})
    assert result["flow_card"] == {}
    assert "scenario_analyst" in result["trace"]
    mock_create_llm.assert_not_called()


@patch("src.nodes.scenario_analyst.create_llm")
def test_node_fails_gracefully_when_phase1_always_errors(mock_create_llm):
    mock_llm = MagicMock()
    select_structured = MagicMock()
    select_structured.side_effect = RuntimeError("LLM timeout")
    mock_llm.with_structured_output.return_value = select_structured
    mock_create_llm.return_value = mock_llm

    result = scenario_analyst({"endpoints": SAMPLE_ENDPOINTS, "raw_scenarios": SAMPLE_SCENARIO})

    assert result["flow_card"] == {}
    assert "validation_errors" in result


@patch("src.nodes.scenario_analyst.create_llm")
def test_node_fails_gracefully_when_phase1_returns_invalid_ids(mock_create_llm):
    mock_llm = MagicMock()
    select_structured = MagicMock()
    # LLM returns a hallucinated op_id
    select_structured.return_value = OperationSelection(
        ordered_operation_ids=["GET /v1/vehicles/available"]
    )
    mock_llm.with_structured_output.return_value = select_structured
    mock_create_llm.return_value = mock_llm

    result = scenario_analyst({"endpoints": SAMPLE_ENDPOINTS, "raw_scenarios": SAMPLE_SCENARIO})

    assert result["flow_card"] == {}
    assert "validation_errors" in result


@patch("src.nodes.scenario_analyst.create_llm")
def test_node_retries_phase1_on_error(mock_create_llm):
    mock_llm = MagicMock()

    select_structured = MagicMock()
    select_structured.side_effect = [RuntimeError("fail"), SAMPLE_SELECTION]

    bind_structured = MagicMock()
    bind_structured.return_value = SAMPLE_STEP_INPUTS

    mock_llm.with_structured_output.side_effect = [select_structured, bind_structured]
    mock_create_llm.return_value = mock_llm

    result = scenario_analyst({"endpoints": SAMPLE_ENDPOINTS, "raw_scenarios": SAMPLE_SCENARIO})

    assert result["flow_card"]["steps"][0]["operation_id"] == "searchAvailableCars"


@patch("src.nodes.scenario_analyst.create_llm")
def test_node_fails_gracefully_when_phase2_always_errors(mock_create_llm):
    mock_llm = MagicMock()

    select_structured = MagicMock()
    select_structured.return_value = SAMPLE_SELECTION

    bind_structured = MagicMock()
    bind_structured.side_effect = RuntimeError("binding failed")

    mock_llm.with_structured_output.side_effect = [select_structured, bind_structured]
    mock_create_llm.return_value = mock_llm

    result = scenario_analyst({"endpoints": SAMPLE_ENDPOINTS, "raw_scenarios": SAMPLE_SCENARIO})

    assert result["flow_card"] == {}
    assert "validation_errors" in result


# ──────────── Структурные проверки выходного FlowCard ──────────────────────

@patch("src.nodes.scenario_analyst.create_llm")
def test_flow_card_steps_have_depends_on(mock_create_llm):
    """Каждый шаг получает depends_on из всех предыдущих шагов."""
    two_step_endpoints = [
        *SAMPLE_ENDPOINTS,
        {
            "operation_id": "createReservationDraft",
            "method": "POST",
            "path": "/api/v1/reservations/drafts",
            "path_params": [],
            "query_params": [],
            "required_fields": ["carId", "customerId"],
            "constraints": {},
            "request_schema": None,
            "response_schemas": {"201": {"properties": {"reservationDraftId": {}}}},
        },
    ]

    selection_2 = OperationSelection(
        ordered_operation_ids=["searchAvailableCars", "createReservationDraft"]
    )
    bind_step2 = StepInputs(
        inputs=[
            VariableBinding(name="carId", source=VarSource.FROM_STEP,
                            source_ref="step_01", source_field="$.items[0].carId",
                            target_location="body.carId"),
            VariableBinding(name="customerId", source=VarSource.ENV,
                            value="customerId", target_location="body.customerId"),
        ],
        produces=["$.reservationDraftId"],
    )

    mock_llm = MagicMock()
    select_structured = MagicMock()
    select_structured.return_value = selection_2

    bind1 = MagicMock()
    bind1.return_value = SAMPLE_STEP_INPUTS
    bind2 = MagicMock()
    bind2.return_value = bind_step2

    # with_structured_output called 3 times: phase1 select + phase2 bind×2
    mock_llm.with_structured_output.side_effect = [select_structured, bind1, bind2]
    mock_create_llm.return_value = mock_llm

    result = scenario_analyst({"endpoints": two_step_endpoints, "raw_scenarios": SAMPLE_SCENARIO})

    steps = result["flow_card"]["steps"]
    assert len(steps) == 2
    assert steps[0]["depends_on"] == []
    assert steps[1]["depends_on"] == ["step_01"]


@patch("src.nodes.scenario_analyst.create_llm")
def test_flow_card_is_json_serialisable(mock_create_llm):
    import json
    mock_llm = MagicMock()
    select_structured = MagicMock()
    select_structured.return_value = SAMPLE_SELECTION
    bind_structured = MagicMock()
    bind_structured.return_value = SAMPLE_STEP_INPUTS
    mock_llm.with_structured_output.side_effect = [select_structured, bind_structured]
    mock_create_llm.return_value = mock_llm

    result = scenario_analyst({"endpoints": SAMPLE_ENDPOINTS, "raw_scenarios": SAMPLE_SCENARIO})

    serialised = json.dumps(result["flow_card"], default=str)
    reloaded = json.loads(serialised)
    assert reloaded["steps"][0]["step_id"] == "step_01"
