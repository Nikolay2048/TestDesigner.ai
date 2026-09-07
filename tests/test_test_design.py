import json

import pytest
from pydantic import ValidationError

from agents.test_designer import TestDesignerAgent, _add_business_attacks_with_llm
from domain import BusinessRuleAttackIdea, ProjectState, TestDesignField as DesignField
from postman_export import PostmanExporter
from test_design import _field_mutations, build_case_execution_plan, build_test_design


@pytest.fixture
def state():
    operation = {"method": "GET", "path": "/appointments/{appointmentId}"}
    step = {"business_step": "Read appointment", "operation": operation,
            "request_bindings": [{"target": "$.path.appointmentId", "location": "path", "source": "literal", "literal": "apt-1"}]}
    plan = {"steps": [step]}
    return ProjectState.model_validate({
        "scenario": {"path": "scenario.md", "title": "Read appointment", "text": "Read appointment"},
        "operations": [{**operation, "operation_id": "read", "request_parameters": [{"name": "appointmentId", "in": "path", "required": True, "schema": {"type": "string"}}], "response_statuses": ["200", "404"]}],
        "data_binding": plan,
        "stabilization": {"status": "passed", "stable_plan": plan, "attempts": [{"attempt": 1, "trace": {"attempt": 1, "base_url": "http://localhost", "status": "passed", "steps": [{"step_id": "s01", "business_step": "Read appointment", "operation": operation, "resolved_path": "/appointments/apt-1", "status": "passed"}]}}]},
    })


def attack(**changes):
    return {"rule_id": "BR-1", "title": "Unknown appointment", "intent": "Check missing resource",
            "mutation_type": "replace_binding_value", "target_step_id": "s01", "target": "$.path.appointmentId",
            "generator": "uuid", "repeat_count": None, **changes}


def test_null_count_is_only_normalized_for_non_repeat_attacks():
    assert BusinessRuleAttackIdea.model_validate(attack()).repeat_count == 1
    for count in (None, 0, 1):
        with pytest.raises(ValidationError):
            BusinessRuleAttackIdea.model_validate(attack(mutation_type="repeat_step", repeat_count=count))
    assert BusinessRuleAttackIdea.model_validate(attack(mutation_type="repeat_step", repeat_count=2)).repeat_count == 2


def test_mixed_llm_batch_keeps_valid_ideas(state):
    state.test_design = build_test_design(state)
    class LLM:
        def complete(self, prompt):
            return json.dumps({"ideas": [attack(), attack(mutation_type="invalid")], "risks": None})
    notes = _add_business_attacks_with_llm(state, LLM(), [[]])
    assert len(state.test_design.test_cases) == 2
    assert any("idea 2 ignored" in risk for risk in state.test_design.risks)
    assert "LLM business-rule attacks proposed: 2." in notes


def test_path_only_scenario_generates_exportable_case_without_llm(state):
    state, run = TestDesignerAgent().run(state)
    assert run.status == "completed"
    assert state.test_design.basis.fields[0].happy_value == "apt-1"
    case, = state.test_design.test_cases
    assert case.expected.status == 404
    plan = build_case_execution_plan(state.stabilization.stable_plan, case)
    assert plan.steps[0].request_bindings[0].generator == "uuid"
    assert state.data_binding.steps[0].request_bindings[0].literal == "apt-1"
    exported = PostmanExporter().export(state, "http://localhost")
    assert exported["summary"]["test_cases"] == 1
    assert exported["test_cases_collection"]["item"]


def test_string_constraints_and_required_query():
    field = DesignField(step_id="s01", business_step="Search", operation={"method": "GET", "path": "/search"},
                        target="$.query.email", location="query", type="string",
                        field_schema={"type": "string", "minLength": 3, "maxLength": 20, "format": "email"})
    mutations = _field_mutations(field)
    assert any(m.action == "omit_field" for m, *_ in mutations)
    assert {m.value for m, *_ in mutations if m.action == "set_value"} == {"xx", "x" * 21, "__INVALID_FORMAT__"}


def test_empty_design_is_not_reported_completed(state):
    state.data_binding.steps[0].request_bindings = []
    state, run = TestDesignerAgent().run(state)
    assert run.status == "failed"
    assert not state.test_design.test_cases


def test_no_lookup_case_without_documented_not_found_response(state):
    state.operations[0].response_statuses = ["200", "422"]
    assert not build_test_design(state).test_cases


@pytest.mark.parametrize("schema", [{"type": "string", "enum": ["apt-1"]},
                                   {"type": "string", "pattern": "^apt-[0-9]+$"},
                                   {"type": "string", "maxLength": 10}])
def test_lookup_mutation_does_not_violate_identifier_constraints(state, schema):
    state.operations[0].request_parameters[0]["schema"] = schema
    assert all(case.technique != "resource_not_found" for case in build_test_design(state).test_cases)


def test_repeated_lookup_does_not_duplicate_resource_case(state):
    state.data_binding.steps.append(state.data_binding.steps[0].model_copy(deep=True))
    assert len(build_test_design(state).test_cases) == 1
