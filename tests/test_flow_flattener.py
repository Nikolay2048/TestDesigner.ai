"""
Тесты для flow_flattener (Этап 8 — inter-flow композиция).

Алгоритм детерминированный (топосорт) — тестируется без моков.
"""

import pytest

from src.models.flow import FlowCard, ScenarioStep, VariableBinding, VarSource
from src.utils.flow_flattener import (
    FlowCycleError,
    flatten_setup_chain,
    topological_order,
)


# ─────────────── Fixtures ───────────────────────────────────────────────────

def _make_flow(flow_id: str, requires: list[str] = None, n_steps: int = 1) -> FlowCard:
    steps = [
        ScenarioStep(
            step_id=f"{flow_id}_step_{i}",
            operation_id=f"op_{flow_id}_{i}",
            inputs=[VariableBinding(name="x", source=VarSource.STATIC, value="1")],
            produces=[],
        )
        for i in range(1, n_steps + 1)
    ]
    return FlowCard(
        flow_id=flow_id,
        name=flow_id,
        description="",
        steps=steps,
        requires_flows=requires or [],
        is_stabilized=True,
    )


FLOW_A = _make_flow("flow_A")                  # independent
FLOW_B = _make_flow("flow_B", ["flow_A"])       # B requires A
FLOW_C = _make_flow("flow_C", ["flow_A"])       # C requires A
FLOW_D = _make_flow("flow_D", ["flow_B", "flow_C"])  # D requires B and C

ALL_FLOWS = {
    "flow_A": FLOW_A,
    "flow_B": FLOW_B,
    "flow_C": FLOW_C,
    "flow_D": FLOW_D,
}


# ─────────────── topological_order ──────────────────────────────────────────

def test_topo_no_dependencies_returns_empty():
    assert topological_order("flow_A", ALL_FLOWS) == []


def test_topo_single_dependency():
    order = topological_order("flow_B", ALL_FLOWS)
    assert order == ["flow_A"]


def test_topo_excludes_start_flow():
    order = topological_order("flow_B", ALL_FLOWS)
    assert "flow_B" not in order


def test_topo_transitive_dependency():
    # D requires [B, C], B requires [A], C requires [A]
    # A must come before B and C
    order = topological_order("flow_D", ALL_FLOWS)
    assert "flow_A" in order
    assert "flow_B" in order
    assert "flow_C" in order
    assert order.index("flow_A") < order.index("flow_B")
    assert order.index("flow_A") < order.index("flow_C")


def test_topo_no_duplicate_when_shared_dependency():
    # A is required by both B and C, should appear only once in D's deps
    order = topological_order("flow_D", ALL_FLOWS)
    assert order.count("flow_A") == 1


def test_topo_cycle_raises_error():
    flow_x = _make_flow("flow_X", ["flow_Y"])
    flow_y = _make_flow("flow_Y", ["flow_X"])
    cyclic = {"flow_X": flow_x, "flow_Y": flow_y}
    with pytest.raises(FlowCycleError):
        topological_order("flow_X", cyclic)


def test_topo_self_reference_raises_error():
    flow_self = _make_flow("flow_self", ["flow_self"])
    with pytest.raises(FlowCycleError):
        topological_order("flow_self", {"flow_self": flow_self})


def test_topo_missing_dependency_raises_key_error():
    flow_broken = _make_flow("flow_broken", ["nonexistent_flow"])
    with pytest.raises(KeyError, match="nonexistent_flow"):
        topological_order("flow_broken", {"flow_broken": flow_broken})


# ─────────────── flatten_setup_chain ────────────────────────────────────────

def test_flatten_no_requires_returns_empty():
    result = flatten_setup_chain(FLOW_A, ALL_FLOWS)
    assert result == []


def test_flatten_single_dependency_returns_its_steps():
    result = flatten_setup_chain(FLOW_B, ALL_FLOWS)
    assert len(result) == len(FLOW_A.steps)
    assert result[0].step_id == FLOW_A.steps[0].step_id


def test_flatten_transitive_returns_all_steps_in_order():
    # D requires B and C, which both require A
    # Expected order: A's steps, then B's or C's (both valid), then the other
    result = flatten_setup_chain(FLOW_D, ALL_FLOWS)
    step_ids = [s.step_id for s in result]
    # A's steps must come before B's and C's steps
    a_idx = step_ids.index(FLOW_A.steps[0].step_id)
    b_idx = step_ids.index(FLOW_B.steps[0].step_id)
    c_idx = step_ids.index(FLOW_C.steps[0].step_id)
    assert a_idx < b_idx
    assert a_idx < c_idx


def test_flatten_no_duplicate_steps_when_shared_dependency():
    # A appears only once despite being required by both B and C
    result = flatten_setup_chain(FLOW_D, ALL_FLOWS)
    a_step_ids = [s.step_id for s in FLOW_A.steps]
    for sid in a_step_ids:
        assert [s.step_id for s in result].count(sid) == 1


def test_flatten_multi_step_flow_includes_all_steps():
    flow_multi = _make_flow("flow_multi", n_steps=3)
    flow_target = _make_flow("flow_target", requires=["flow_multi"])
    flows = {"flow_multi": flow_multi, "flow_target": flow_target}
    result = flatten_setup_chain(flow_target, flows)
    assert len(result) == 3


def test_flatten_does_not_include_target_steps():
    result = flatten_setup_chain(FLOW_B, ALL_FLOWS)
    b_step_ids = {s.step_id for s in FLOW_B.steps}
    result_ids = {s.step_id for s in result}
    assert result_ids.isdisjoint(b_step_ids)


def test_flatten_cycle_raises_error():
    flow_x = _make_flow("flow_X", ["flow_Y"])
    flow_y = _make_flow("flow_Y", ["flow_X"])
    with pytest.raises(FlowCycleError):
        flatten_setup_chain(flow_x, {"flow_X": flow_x, "flow_Y": flow_y})


# ─────────────── Integration: test_designer uses inter-flow setup ────────────

from unittest.mock import MagicMock, patch
from src.nodes.test_designer import (
    _LLMTechniqueResult,
    _StateTechniqueResult,
    test_designer as run_test_designer,
)


def _make_stable_card(flow_id: str, requires: list[str] = None) -> dict:
    return {
        "flow_id": flow_id,
        "name": flow_id,
        "description": "",
        "requires_flows": requires or [],
        "steps": [{
            "step_id": f"{flow_id}_step_01",
            "operation_id": "searchAvailableCars",
            "inputs": [
                {"name": "cityId", "source": "static", "value": "77",
                 "target_location": "query.cityId"},
            ],
            "produces": [],
            "depends_on": [],
        }],
        "exports": [],
        "teardown_steps": [],
        "is_stabilized": True,
        "stabilization_log": [],
    }


PREREQ_CARD = _make_stable_card("prereq_flow")
TARGET_CARD = _make_stable_card("target_flow", requires=["prereq_flow"])

ENDPOINTS = [{
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "path": "/api/v1/cars/availability",
    "query_params": [{"name": "cityId", "required": True}],
    "required_fields": [],
    "constraints": {"cityId": {"enum": [36, 77]}},
    "response_schemas": {},
}]


def _empty_llm_mock():
    mock_llm = MagicMock()
    llm_result = _LLMTechniqueResult(cases=[])
    state_result = _StateTechniqueResult(cases=[])

    def side_effect(output_model):
        mock_s = MagicMock()
        mock_s.return_value = state_result if output_model is _StateTechniqueResult else llm_result
        return mock_s

    mock_llm.with_structured_output.side_effect = side_effect
    return mock_llm


@patch("src.nodes.test_designer.create_llm")
def test_inter_flow_setup_prepended_to_all_cases(mock_create_llm):
    mock_create_llm.return_value = _empty_llm_mock()

    state = {
        "stabilized_card": TARGET_CARD,
        "all_stabilized_cards": [PREREQ_CARD, TARGET_CARD],
        "endpoints": ENDPOINTS,
        "raw_scenarios": "Search for cars.",
    }
    result = run_test_designer(state)

    assert len(result["test_cases"]) > 0
    prereq_step_id = "prereq_flow_step_01"
    for tc in result["test_cases"]:
        setup_ids = [s["step_id"] for s in tc["setup_chain"]]
        assert prereq_step_id in setup_ids, (
            f"Expected {prereq_step_id!r} in setup_chain for {tc['title']!r}, got {setup_ids}"
        )


@patch("src.nodes.test_designer.create_llm")
def test_no_inter_flow_setup_when_no_requires(mock_create_llm):
    mock_create_llm.return_value = _empty_llm_mock()

    state = {
        "stabilized_card": PREREQ_CARD,  # no requires_flows
        "all_stabilized_cards": [PREREQ_CARD],
        "endpoints": ENDPOINTS,
        "raw_scenarios": "Search for cars.",
    }
    result = run_test_designer(state)

    # Happy path: setup_chain should only have intra-flow preceding steps (none for 1-step flow)
    happy = next(tc for tc in result["test_cases"] if tc["technique"] == "happy_path")
    assert happy["setup_chain"] == []
