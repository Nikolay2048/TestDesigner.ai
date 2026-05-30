"""
Тесты для Test Designer.

Детерминированные генераторы (happy_path, boundary, missing_field) — тестируются напрямую.
LLM-генераторы — мокируются: проверяем контракт (правильный вывод), а не качество модели.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.models.flow import ScenarioStep, VariableBinding, VarSource
from src.models.test_design import TestCase, TestTechnique
from src.nodes.test_designer import (
    _apply_mutations,
    _cases_from_llm_result,
    _generate_boundary,
    _generate_happy_path,
    _generate_missing_field,
    _get_success_status,
    _invalid_enum_value,
    _LLMCase,
    _LLMTechniqueResult,
    _StateTechniqueResult,
    _FieldChange,
    _preceding_steps,
    _steps_text,
    test_designer as run_test_designer,
)
from src.models.flow import FlowCard


# ─────────────── Fixture data ───────────────────────────────────────────────

SEARCH_EP = {
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "path": "/api/v1/cars/availability",
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
    "response_schemas": {"200": {}},
}

DRAFT_EP = {
    "operation_id": "createReservationDraft",
    "method": "POST",
    "path": "/api/v1/reservations/drafts",
    "query_params": [],
    "required_fields": ["carId", "tariff"],
    "constraints": {
        "tariff": {"enum": ["DAILY", "HOURLY"]},
        "promoCode": {"maxLength": 20},
    },
    "response_schemas": {"201": {}},
}

SEARCH_STEP = ScenarioStep(
    step_id="step_01",
    operation_id="searchAvailableCars",
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

DRAFT_STEP = ScenarioStep(
    step_id="step_02",
    operation_id="createReservationDraft",
    inputs=[
        VariableBinding(name="carId", source=VarSource.FROM_STEP,
                        source_ref="step_01", source_field="$.items[0].carId",
                        target_location="body.carId"),
        VariableBinding(name="tariff", source=VarSource.STATIC, value="DAILY",
                        target_location="body.tariff"),
        VariableBinding(name="promoCode", source=VarSource.STATIC, value="PROMO10",
                        target_location="body.promoCode"),
    ],
    produces=["$.draftId"],
)

FLOW_CARD = FlowCard(
    flow_id="test_flow",
    name="Search and Reserve",
    description="",
    steps=[SEARCH_STEP, DRAFT_STEP],
    is_stabilized=True,
)

EP_MAP = {
    "searchAvailableCars": SEARCH_EP,
    "createReservationDraft": DRAFT_EP,
}

STEP_MAP = {
    "step_01": SEARCH_STEP,
    "step_02": DRAFT_STEP,
}


# ─────────────── _get_success_status ────────────────────────────────────────

def test_success_status_default_is_200():
    assert _get_success_status({}) == 200


def test_success_status_is_201_when_in_schema():
    assert _get_success_status({"response_schemas": {"201": {}}}) == 201


def test_success_status_200_when_only_other_codes():
    assert _get_success_status({"response_schemas": {"200": {}, "400": {}}}) == 200


# ─────────────── _invalid_enum_value ────────────────────────────────────────

def test_invalid_enum_returns_value_not_in_list():
    result = _invalid_enum_value([36, 77])
    assert str(result) not in {"36", "77"}


def test_invalid_enum_strings():
    result = _invalid_enum_value(["ECONOMY", "COMFORT"])
    assert result not in {"ECONOMY", "COMFORT"}


# ─────────────── _preceding_steps ───────────────────────────────────────────

def test_preceding_steps_first_step_has_none():
    assert _preceding_steps(FLOW_CARD, "step_01") == []


def test_preceding_steps_second_step_has_first():
    result = _preceding_steps(FLOW_CARD, "step_02")
    assert len(result) == 1
    assert result[0].step_id == "step_01"


# ─────────────── _apply_mutations ───────────────────────────────────────────

def test_apply_mutations_changes_static_field():
    result = _apply_mutations(SEARCH_STEP, {"cityId": "36"}, set())
    city = next(b for b in result if b.name == "cityId")
    assert city.value == "36"
    assert city.source == VarSource.STATIC


def test_apply_mutations_leaves_other_fields_intact():
    result = _apply_mutations(SEARCH_STEP, {"cityId": "36"}, set())
    date = next(b for b in result if b.name == "dateFrom")
    assert date.source == VarSource.GENERATED
    assert date.generator == "future_datetime"


def test_apply_mutations_removes_field():
    result = _apply_mutations(SEARCH_STEP, {}, {"cityId"})
    names = [b.name for b in result]
    assert "cityId" not in names
    assert "dateFrom" in names


def test_apply_mutations_protects_from_step_by_default():
    result = _apply_mutations(DRAFT_STEP, {"carId": "fake-id"}, set())
    car = next(b for b in result if b.name == "carId")
    assert car.source == VarSource.FROM_STEP  # not changed


def test_apply_mutations_allows_from_step_override_when_permitted():
    result = _apply_mutations(DRAFT_STEP, {"carId": "fake-id"}, set(),
                              allow_context_override=True)
    car = next(b for b in result if b.name == "carId")
    assert car.source == VarSource.STATIC
    assert car.value == "fake-id"


def test_apply_mutations_does_not_mutate_original():
    _apply_mutations(SEARCH_STEP, {"cityId": "36"}, set())
    original = next(b for b in SEARCH_STEP.inputs if b.name == "cityId")
    assert original.value == "77"  # unchanged


# ─────────────── _generate_happy_path ───────────────────────────────────────

def test_happy_path_produces_one_case():
    cases = _generate_happy_path(FLOW_CARD, EP_MAP)
    assert len(cases) == 1


def test_happy_path_target_is_last_step():
    cases = _generate_happy_path(FLOW_CARD, EP_MAP)
    assert cases[0].target_step == "step_02"


def test_happy_path_setup_chain_has_preceding_steps():
    cases = _generate_happy_path(FLOW_CARD, EP_MAP)
    assert len(cases[0].setup_chain) == 1
    assert cases[0].setup_chain[0].step_id == "step_01"


def test_happy_path_technique():
    cases = _generate_happy_path(FLOW_CARD, EP_MAP)
    assert cases[0].technique == TestTechnique.HAPPY_PATH


def test_happy_path_success_status_201_for_post():
    cases = _generate_happy_path(FLOW_CARD, EP_MAP)
    assert cases[0].expected_status == 201  # DRAFT_EP has 201


def test_happy_path_empty_for_empty_flow():
    empty = FlowCard(flow_id="x", name="x", description="", steps=[], is_stabilized=True)
    assert _generate_happy_path(empty, {}) == []


def test_happy_path_case_id():
    cases = _generate_happy_path(FLOW_CARD, EP_MAP)
    assert cases[0].case_id == "test_flow_happy_path"


# ─────────────── _generate_boundary ─────────────────────────────────────────

def _boundary_titles(flow_card=FLOW_CARD, ep_map=EP_MAP) -> list[str]:
    return [tc.title for tc in _generate_boundary(flow_card, ep_map)]


def test_boundary_generates_cases_for_constrained_fields():
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    assert len(cases) > 0


def test_boundary_enum_generates_invalid_case():
    titles = _boundary_titles()
    assert any("not in enum" in t and "cityId" in t for t in titles)


def test_boundary_enum_invalid_case_expects_400():
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    enum_cases = [tc for tc in cases if "cityId" in tc.title and "not in enum" in tc.title]
    assert all(tc.expected_status == 400 for tc in enum_cases)


def test_boundary_minimum_generates_below_and_at():
    titles = _boundary_titles()
    assert any("below minimum" in t and "driverAge" in t for t in titles)
    assert any("at minimum" in t and "driverAge" in t for t in titles)


def test_boundary_below_minimum_expects_400():
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    below = next(tc for tc in cases if "below minimum" in tc.title and "driverAge" in tc.title)
    assert below.expected_status == 400


def test_boundary_at_minimum_expects_success():
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    at_min = next(tc for tc in cases if "at minimum" in tc.title and "driverAge" in tc.title)
    assert 200 <= at_min.expected_status < 300


def test_boundary_maximum_generates_above_and_at():
    titles = _boundary_titles()
    assert any("above maximum" in t and "driverAge" in t for t in titles)
    assert any("at maximum" in t and "driverAge" in t for t in titles)


def test_boundary_above_maximum_expects_400():
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    above = next(tc for tc in cases if "above maximum" in tc.title and "driverAge" in tc.title)
    assert above.expected_status == 400


def test_boundary_at_minimum_value_is_correct():
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    at_min = next(tc for tc in cases if "at minimum" in tc.title and "driverAge" in tc.title)
    driver_age = next(b for b in at_min.modified_inputs if b.name == "driverAge")
    assert driver_age.value == "21"


def test_boundary_above_maximum_value_is_correct():
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    above = next(tc for tc in cases if "above maximum" in tc.title and "driverAge" in tc.title)
    driver_age = next(b for b in above.modified_inputs if b.name == "driverAge")
    assert driver_age.value == "76"


def test_boundary_maxlength_generates_at_and_over():
    ep_with_length = {**DRAFT_EP, "constraints": {"promoCode": {"maxLength": 20}}}
    flow = FlowCard(flow_id="f", name="f", description="", steps=[DRAFT_STEP], is_stabilized=True)
    cases = _generate_boundary(flow, {"createReservationDraft": ep_with_length})
    titles = [tc.title for tc in cases]
    assert any("at maxLength" in t and "promoCode" in t for t in titles)
    assert any("over maxLength" in t and "promoCode" in t for t in titles)


def test_boundary_setup_chain_populated_for_second_step():
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    # step_02 boundary cases should have step_01 in setup_chain
    step2_cases = [tc for tc in cases if tc.target_step == "step_02"]
    assert all(len(tc.setup_chain) == 1 for tc in step2_cases)
    assert all(tc.setup_chain[0].step_id == "step_01" for tc in step2_cases)


def test_boundary_skips_from_step_fields():
    # DRAFT_STEP has carId as from_step — boundary should NOT test it
    cases = _generate_boundary(FLOW_CARD, EP_MAP)
    assert not any("carId" in tc.title for tc in cases)


def test_boundary_no_cases_when_no_constraints():
    ep_no_c = {**SEARCH_EP, "constraints": {}}
    flow = FlowCard(flow_id="f", name="f", description="", steps=[SEARCH_STEP], is_stabilized=True)
    assert _generate_boundary(flow, {"searchAvailableCars": ep_no_c}) == []


# ─────────────── _generate_missing_field ────────────────────────────────────

def test_missing_field_generates_case_per_required_field():
    cases = _generate_missing_field(FLOW_CARD, EP_MAP)
    # step_01 required query params: cityId, dateFrom, driverAge (all static/generated)
    # step_02 required body fields: carId (from_step → skip), tariff (static → include)
    step1_cases = [tc for tc in cases if tc.target_step == "step_01"]
    assert len(step1_cases) == 3  # cityId, dateFrom, driverAge


def test_missing_field_skips_from_step_fields():
    cases = _generate_missing_field(FLOW_CARD, EP_MAP)
    # step_02: carId is from_step → should not have a missing-carId case
    step2_cases = [tc for tc in cases if tc.target_step == "step_02"]
    assert not any("carId" in tc.title for tc in step2_cases)


def test_missing_field_step2_has_tariff_case():
    cases = _generate_missing_field(FLOW_CARD, EP_MAP)
    step2_cases = [tc for tc in cases if tc.target_step == "step_02"]
    assert any("tariff" in tc.title for tc in step2_cases)


def test_missing_field_removed_field_not_in_inputs():
    cases = _generate_missing_field(FLOW_CARD, EP_MAP)
    city_case = next(tc for tc in cases if "cityId" in tc.title)
    names = [b.name for b in city_case.modified_inputs]
    assert "cityId" not in names


def test_missing_field_expects_400():
    cases = _generate_missing_field(FLOW_CARD, EP_MAP)
    assert all(tc.expected_status == 400 for tc in cases)


def test_missing_field_technique_is_negative():
    cases = _generate_missing_field(FLOW_CARD, EP_MAP)
    assert all(tc.technique == TestTechnique.NEGATIVE for tc in cases)


# ─────────────── _cases_from_llm_result ─────────────────────────────────────

def test_llm_result_unknown_step_id_is_skipped():
    result = _LLMTechniqueResult(cases=[
        _LLMCase(title="test", target_step_id="nonexistent_step",
                 field_changes=[], expected_status=400, reasoning=""),
    ])
    cases = _cases_from_llm_result(result, TestTechnique.EQUIVALENCE, FLOW_CARD, EP_MAP, STEP_MAP)
    assert cases == []


def test_llm_result_unknown_field_is_dropped():
    result = _LLMTechniqueResult(cases=[
        _LLMCase(
            title="test",
            target_step_id="step_01",
            field_changes=[_FieldChange(field_name="nonExistentField", new_value="x")],
            expected_status=400,
            reasoning="",
        ),
    ])
    cases = _cases_from_llm_result(result, TestTechnique.EQUIVALENCE, FLOW_CARD, EP_MAP, STEP_MAP)
    assert len(cases) == 1  # case still created, but field change dropped


def test_llm_result_applies_field_change():
    result = _LLMTechniqueResult(cases=[
        _LLMCase(
            title="Economy class test",
            target_step_id="step_01",
            field_changes=[_FieldChange(field_name="cityId", new_value="36")],
            expected_status=200,
            reasoning="",
        ),
    ])
    cases = _cases_from_llm_result(result, TestTechnique.EQUIVALENCE, FLOW_CARD, EP_MAP, STEP_MAP)
    assert len(cases) == 1
    city = next(b for b in cases[0].modified_inputs if b.name == "cityId")
    assert city.value == "36"


def test_llm_result_protects_from_step_by_default():
    result = _LLMTechniqueResult(cases=[
        _LLMCase(
            title="Test with fake carId",
            target_step_id="step_02",
            field_changes=[_FieldChange(field_name="carId", new_value="fake-car")],
            expected_status=404,
            reasoning="",
        ),
    ])
    cases = _cases_from_llm_result(result, TestTechnique.EQUIVALENCE, FLOW_CARD, EP_MAP, STEP_MAP)
    car = next(b for b in cases[0].modified_inputs if b.name == "carId")
    assert car.source == VarSource.FROM_STEP  # not overridden


def test_llm_result_allows_from_step_override():
    result = _LLMTechniqueResult(cases=[
        _LLMCase(
            title="Test with fake carId",
            target_step_id="step_02",
            field_changes=[_FieldChange(field_name="carId", new_value="fake-car")],
            expected_status=404,
            reasoning="",
        ),
    ])
    cases = _cases_from_llm_result(
        result, TestTechnique.NEGATIVE, FLOW_CARD, EP_MAP, STEP_MAP,
        allow_context_override=True,
    )
    car = next(b for b in cases[0].modified_inputs if b.name == "carId")
    assert car.source == VarSource.STATIC
    assert car.value == "fake-car"


# ─────────────── test_designer node ─────────────────────────────────────────

STABLE_CARD = {
    "flow_id": "test_flow",
    "name": "Search and Reserve",
    "description": "",
    "steps": [
        {
            "step_id": "step_01",
            "operation_id": "searchAvailableCars",
            "inputs": [
                {"name": "cityId", "source": "static", "value": "77",
                 "target_location": "query.cityId"},
                {"name": "dateFrom", "source": "generated", "generator": "future_datetime",
                 "target_location": "query.dateFrom"},
                {"name": "driverAge", "source": "static", "value": "30",
                 "target_location": "query.driverAge"},
            ],
            "produces": ["$.items[0].carId"],
            "depends_on": [],
        },
        {
            "step_id": "step_02",
            "operation_id": "createReservationDraft",
            "inputs": [
                {"name": "carId", "source": "from_step", "source_ref": "step_01",
                 "source_field": "$.items[0].carId", "target_location": "body.carId"},
                {"name": "tariff", "source": "static", "value": "DAILY",
                 "target_location": "body.tariff"},
            ],
            "produces": ["$.draftId"],
            "depends_on": ["step_01"],
        },
    ],
    "exports": [],
    "teardown_steps": [],
    "is_stabilized": True,
    "stabilization_log": [],
}

ENDPOINTS = [SEARCH_EP, DRAFT_EP]
RAW_SCENARIOS = "User searches for cars in city 77 or 36, then creates a reservation draft."


def _make_llm_mock(cases: list[_LLMCase]) -> MagicMock:
    """Mock that returns _LLMTechniqueResult for equivalence/negative and empty _StateTechniqueResult for state_based."""
    mock_llm = MagicMock()
    llm_result = _LLMTechniqueResult(cases=cases)
    state_result = _StateTechniqueResult(cases=[])

    def structured_side_effect(output_model):
        mock_s = MagicMock()
        mock_s.return_value = state_result if output_model is _StateTechniqueResult else llm_result
        return mock_s

    mock_llm.with_structured_output.side_effect = structured_side_effect
    return mock_llm


@patch("src.nodes.test_designer.create_llm")
def test_node_returns_cases(mock_create_llm):
    mock_create_llm.return_value = _make_llm_mock([])

    state = {"stabilized_card": STABLE_CARD, "endpoints": ENDPOINTS, "raw_scenarios": RAW_SCENARIOS}
    result = run_test_designer(state)

    assert "test_cases" in result
    assert "trace" in result
    assert len(result["test_cases"]) > 0


@patch("src.nodes.test_designer.create_llm")
def test_node_includes_happy_path(mock_create_llm):
    mock_create_llm.return_value = _make_llm_mock([])

    state = {"stabilized_card": STABLE_CARD, "endpoints": ENDPOINTS, "raw_scenarios": RAW_SCENARIOS}
    result = run_test_designer(state)

    techniques = {tc["technique"] for tc in result["test_cases"]}
    assert TestTechnique.HAPPY_PATH.value in techniques


@patch("src.nodes.test_designer.create_llm")
def test_node_includes_boundary(mock_create_llm):
    mock_create_llm.return_value = _make_llm_mock([])

    state = {"stabilized_card": STABLE_CARD, "endpoints": ENDPOINTS, "raw_scenarios": RAW_SCENARIOS}
    result = run_test_designer(state)

    techniques = {tc["technique"] for tc in result["test_cases"]}
    assert TestTechnique.BOUNDARY.value in techniques


@patch("src.nodes.test_designer.create_llm")
def test_node_includes_negative(mock_create_llm):
    mock_create_llm.return_value = _make_llm_mock([])

    state = {"stabilized_card": STABLE_CARD, "endpoints": ENDPOINTS, "raw_scenarios": RAW_SCENARIOS}
    result = run_test_designer(state)

    techniques = {tc["technique"] for tc in result["test_cases"]}
    assert TestTechnique.NEGATIVE.value in techniques


def test_node_empty_when_not_stabilized():
    state = {
        "stabilized_card": {**STABLE_CARD, "is_stabilized": False},
        "endpoints": ENDPOINTS,
    }
    result = run_test_designer(state)
    assert result["test_cases"] == []


def test_node_empty_when_no_card():
    result = run_test_designer({"stabilized_card": {}, "endpoints": []})
    assert result["test_cases"] == []


@patch("src.nodes.test_designer.create_llm")
def test_node_includes_llm_cases_when_provided(mock_create_llm):
    llm_case = _LLMCase(
        title="Economy class — different city",
        target_step_id="step_01",
        field_changes=[_FieldChange(field_name="cityId", new_value="36")],
        expected_status=200,
        reasoning="Test with second valid city",
    )
    mock_create_llm.return_value = _make_llm_mock([llm_case])

    state = {"stabilized_card": STABLE_CARD, "endpoints": ENDPOINTS, "raw_scenarios": RAW_SCENARIOS}
    result = run_test_designer(state)

    equivalence_cases = [tc for tc in result["test_cases"] if tc["technique"] == "equivalence"]
    assert len(equivalence_cases) >= 1


@patch("src.nodes.test_designer.create_llm")
def test_node_llm_failure_still_returns_code_cases(mock_create_llm):
    """LLM вылетает — детерминированные кейсы всё равно возвращаются."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.side_effect = RuntimeError("LLM unavailable")
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    state = {"stabilized_card": STABLE_CARD, "endpoints": ENDPOINTS, "raw_scenarios": RAW_SCENARIOS}
    result = run_test_designer(state)

    assert len(result["test_cases"]) > 0
    techniques = {tc["technique"] for tc in result["test_cases"]}
    assert TestTechnique.HAPPY_PATH.value in techniques
    assert TestTechnique.BOUNDARY.value in techniques
    assert TestTechnique.NEGATIVE.value in techniques


@patch("src.nodes.test_designer.create_llm")
def test_node_all_cases_are_valid_test_case_dicts(mock_create_llm):
    mock_create_llm.return_value = _make_llm_mock([])

    state = {"stabilized_card": STABLE_CARD, "endpoints": ENDPOINTS, "raw_scenarios": RAW_SCENARIOS}
    result = run_test_designer(state)

    required_keys = {"case_id", "flow_id", "technique", "title", "target_step",
                     "setup_chain", "modified_inputs", "expected_status", "assertions", "group"}
    for tc in result["test_cases"]:
        assert required_keys.issubset(tc.keys()), f"Missing keys in: {tc}"


@patch("src.nodes.test_designer.create_llm")
def test_node_case_ids_are_unique(mock_create_llm):
    mock_create_llm.return_value = _make_llm_mock([])

    state = {"stabilized_card": STABLE_CARD, "endpoints": ENDPOINTS, "raw_scenarios": RAW_SCENARIOS}
    result = run_test_designer(state)

    ids = [tc["case_id"] for tc in result["test_cases"]]
    assert len(ids) == len(set(ids)), "Duplicate case_ids found"
