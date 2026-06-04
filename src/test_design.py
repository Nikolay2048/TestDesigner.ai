from __future__ import annotations

import copy
from typing import Any

from domain import (
    ApiOperation,
    BusinessRuleAttackIdea,
    DataBindingPlan,
    DesignedTestCase,
    ExecutorTrace,
    ProjectState,
    RequestValueBinding,
    TestBasis,
    TestCaseExecutionRecord,
    TestAssertion,
    TestDesignField,
    TestDesignResult,
    TestDesignRule,
    TestExpectation,
    TestIdea,
    TestMutation,
)
from executor import FlowExecutor
from generators import GeneratorRegistry


def build_test_design(state: ProjectState) -> TestDesignResult:
    """Build a first reviewable test design as mutations of the stable happy path."""

    basis = build_test_basis(state)
    ideas = build_test_ideas(basis, state)
    cases = assemble_test_cases(ideas, state)
    executions = plan_test_case_executions(cases)
    risks = list(basis.risks)
    if not ideas:
        risks.append("No test ideas were generated from the current stable happy path.")
    return TestDesignResult(basis=basis, ideas=ideas, test_cases=cases, executions=executions, risks=risks)


def append_business_rule_attack_ideas(
    test_design: TestDesignResult,
    attacks: list[BusinessRuleAttackIdea],
    state: ProjectState,
) -> list[str]:
    """Convert LLM business-rule attacks into validated executable test ideas."""

    notes = []
    start = len(test_design.ideas) + 1
    accepted = 0
    for attack in attacks:
        idea = _business_attack_to_idea(attack, test_design.basis, state, start + accepted)
        if not idea:
            notes.append(f"Ignored non-executable business attack: {attack.title or attack.intent}")
            continue
        test_design.ideas.append(idea)
        accepted += 1

    test_design.ideas = _dedupe_ideas(test_design.ideas)
    test_design.test_cases = assemble_test_cases(test_design.ideas, state)
    test_design.executions = plan_test_case_executions(test_design.test_cases)
    notes.append(f"Business-rule executable attacks accepted: {accepted}.")
    return notes


def build_test_basis(state: ProjectState) -> TestBasis:
    if not state.data_binding:
        return TestBasis(risks=["No data binding plan is available for test design."])
    if not state.stabilization or state.stabilization.status != "passed" or not state.stabilization.attempts:
        return TestBasis(risks=["Stable happy path is required before test design."])

    latest_trace = state.stabilization.attempts[-1].trace
    operation_by_key = {(item.method.upper(), item.path): item for item in state.operations}
    fields = []

    for index, step in enumerate(state.data_binding.steps, start=1):
        step_id = f"s{index:02d}"
        operation = operation_by_key.get((step.operation.method.upper(), step.operation.path))
        trace_step = _trace_step(latest_trace, step_id)
        for binding in step.request_bindings:
            schema = _schema_for_binding(operation, binding.target) if operation else {}
            field = TestDesignField(
                step_id=step_id,
                business_step=step.business_step,
                operation=step.operation,
                target=binding.target,
                location=binding.location,
                type=_schema_type(schema),
                required=_is_required(operation, binding.target) if operation else True,
                field_schema=schema,
                happy_value=_happy_value(trace_step, binding.location, binding.target),
                binding_source=binding.source,
                techniques=_techniques_for_field(schema, binding.location),
            )
            fields.append(field)

    rules = _rules_from_understanding(state, fields)
    return TestBasis(fields=fields, rules=rules)


def build_test_ideas(basis: TestBasis, state: ProjectState) -> list[TestIdea]:
    ideas: list[TestIdea] = []
    counter = 1

    for field in basis.fields:
        for mutation, technique, title_suffix, reason in _field_mutations(field):
            ideas.append(
                TestIdea(
                    idea_id=f"TI-{counter:03d}",
                    title=f"{field.step_id} {field.target}: {title_suffix}",
                    technique=technique,
                    source=f"{field.step_id}:{field.target}",
                    mutation=mutation,
                    expected=_negative_expectation(
                        state.operations,
                        field.operation.method,
                        field.operation.path,
                        technique,
                    ),
                    requires_human_review=True,
                    reason=reason,
                )
            )
            counter += 1

    return _dedupe_ideas(ideas)


def assemble_test_cases(ideas: list[TestIdea], state: ProjectState | None = None) -> list[DesignedTestCase]:
    cases = []
    for index, idea in enumerate(ideas, start=1):
        setup_until_step = _previous_step_id(idea.mutation.step_id)
        case = DesignedTestCase(
            case_id=f"TC-{index:03d}",
            title=idea.title,
            type=idea.type,
            technique=idea.technique,
            priority=_priority_for_idea(idea),
            preconditions=_preconditions_for_case(setup_until_step),
            steps=_steps_for_case(idea, setup_until_step),
            expected_result=_expected_result_lines(idea.expected),
            setup_until_step=setup_until_step,
            mutated_step_id=idea.mutation.step_id,
            mutation=idea.mutation,
            expected=idea.expected,
            traceability=[idea.idea_id, idea.source, idea.mutation.step_id, idea.mutation.target],
            tags=[idea.technique, idea.type],
            requires_human_review=idea.requires_human_review,
        )
        case.assertions = build_test_case_assertions(case, state)
        cases.append(case)
    return cases


def plan_test_case_executions(cases: list[DesignedTestCase]) -> list[TestCaseExecutionRecord]:
    return [
        TestCaseExecutionRecord(
            case_id=case.case_id,
            status="not_run",
            mode="planned",
            setup_until_step=case.setup_until_step,
            mutated_step_id=case.mutated_step_id,
            expected_status=case.expected.status,
            accepted_statuses=_accepted_statuses(case.expected),
            notes=["Execution is planned but not run. Use --run-test-cases to execute generated cases."],
        )
        for case in cases
    ]


def build_test_case_assertions(case: DesignedTestCase, state: ProjectState | None) -> list[TestAssertion]:
    if not state or not state.stabilization or not state.stabilization.stable_plan:
        return _mutated_step_assertions(case)

    try:
        case_plan = build_case_execution_plan(state.stabilization.stable_plan, case)
    except Exception:
        return _mutated_step_assertions(case)

    operation_by_key = {(item.method.upper(), item.path): item for item in state.operations}
    expected_failure_steps = _expected_failure_step_ids(case)
    assertions: list[TestAssertion] = []
    for step_id, step in _iter_plan_steps(case_plan):
        if step_id in expected_failure_steps:
            assertions.extend(_mutated_step_assertions(case, step_id=step_id))
            continue
        assertions.append(
            TestAssertion(
                assertion_id=_assertion_id(len(assertions) + 1),
                step_id=step_id,
                source="expected_status",
                kind="status_2xx",
                description="Setup step must complete successfully.",
            )
        )
        for extraction in step.response_extractions:
            assertions.append(
                TestAssertion(
                    assertion_id=_assertion_id(len(assertions) + 1),
                    step_id=step_id,
                    source="response_extraction",
                    kind="json_path_exists",
                    json_path=extraction.json_path,
                    description=f"Response value for variable {extraction.variable} must exist.",
                )
            )
        operation = operation_by_key.get((step.operation.method.upper(), step.operation.path))
        if operation:
            assertions.extend(_openapi_response_assertions(operation, step_id, len(assertions) + 1))
    return _dedupe_assertions(assertions)


def execute_test_cases(
    test_design: TestDesignResult,
    stable_plan: DataBindingPlan,
    base_url: str,
    static_test_data: dict[str, Any],
    external_context: dict[str, Any] | None = None,
    external_context_factory=None,
    generator_registry: GeneratorRegistry | None = None,
) -> list[TestCaseExecutionRecord]:
    records = []
    registry = generator_registry or GeneratorRegistry()
    for index, case in enumerate(test_design.test_cases, start=1):
        context = external_context_factory(index) if external_context_factory else external_context or {}
        try:
            case_plan = build_case_execution_plan(stable_plan, case)
        except Exception as exc:
            records.append(
                TestCaseExecutionRecord(
                    case_id=case.case_id,
                    status="blocked",
                    mode="http",
                    setup_until_step=case.setup_until_step,
                    mutated_step_id=case.mutated_step_id,
                    expected_status=case.expected.status,
                    accepted_statuses=_accepted_statuses(case.expected),
                    notes=[str(exc)],
                )
            )
            continue

        trace = FlowExecutor(
            base_url=base_url,
            static_test_data=static_test_data,
            external_context=context,
            generator_registry=registry,
        ).execute(case_plan, attempt=index)
        records.append(_execution_record_from_trace(case, trace))
    return records


def build_case_execution_plan(stable_plan: DataBindingPlan, case: DesignedTestCase) -> DataBindingPlan:
    try:
        mutated_index = int(case.mutated_step_id.removeprefix("s")) - 1
    except ValueError as exc:
        raise ValueError(f"Invalid mutated step id: {case.mutated_step_id}") from exc
    if mutated_index < 0 or mutated_index >= len(stable_plan.steps):
        raise ValueError(f"Mutated step is outside stable plan: {case.mutated_step_id}")

    if case.mutation.action == "skip_setup_step":
        return _build_skip_setup_plan(stable_plan, case, mutated_index)
    if case.mutation.action == "repeat_step":
        return _build_repeat_step_plan(stable_plan, case, mutated_index)

    plan = copy.deepcopy(DataBindingPlan(steps=stable_plan.steps[: mutated_index + 1]))
    step = plan.steps[mutated_index]
    if case.mutation.action == "omit_field":
        before = len(step.request_bindings)
        step.request_bindings = [
            binding for binding in step.request_bindings if not _targets_equal(binding.target, case.mutation.target)
        ]
        if len(step.request_bindings) == before:
            raise ValueError(f"Cannot omit unknown request binding: {case.mutation.target}")
    elif case.mutation.action == "set_value":
        binding = next(
            (item for item in step.request_bindings if _targets_equal(item.target, case.mutation.target)),
            None,
        )
        if not binding:
            raise ValueError(f"Cannot set unknown request binding: {case.mutation.target}")
        _set_literal_binding(binding, case.mutation.value)
    elif case.mutation.action == "replace_binding_value":
        binding = next(
            (item for item in step.request_bindings if _targets_equal(item.target, case.mutation.target)),
            None,
        )
        if not binding:
            raise ValueError(f"Cannot replace unknown request binding: {case.mutation.target}")
        if not case.mutation.replacement:
            raise ValueError(f"Replacement binding is missing for {case.mutation.target}")
        _replace_binding(binding, case.mutation.replacement)
    elif case.mutation.action == "replace_static_data":
        binding = next(
            (item for item in step.request_bindings if _targets_equal(item.target, case.mutation.target)),
            None,
        )
        if not binding:
            raise ValueError(f"Cannot replace unknown request binding: {case.mutation.target}")
        if not case.mutation.replacement or case.mutation.replacement.source != "static":
            raise ValueError(f"Static replacement is missing for {case.mutation.target}")
        _replace_binding(binding, case.mutation.replacement)
    else:
        raise ValueError(f"Unsupported mutation action: {case.mutation.action}")
    return plan


def _field_mutations(field: TestDesignField) -> list[tuple[TestMutation, str, str, str]]:
    mutations: list[tuple[TestMutation, str, str, str]] = []
    if field.required and field.location == "body":
        mutations.append(
            (
                TestMutation(step_id=field.step_id, target=field.target, action="omit_field"),
                "required_field_omission",
                "omit required field",
                "Required request body field should be tested when absent.",
            )
        )

    enum_values = field.field_schema.get("enum") if isinstance(field.field_schema.get("enum"), list) else []
    if enum_values:
        mutations.append(
            (
                TestMutation(step_id=field.step_id, target=field.target, action="set_value", value="__INVALID_ENUM__"),
                "equivalence_partitioning",
                "use invalid enum value",
                "Enum field has valid classes; invalid class should be tested.",
            )
        )
        if len(enum_values) > 1:
            mutations.append(
                (
                    TestMutation(step_id=field.step_id, target=field.target, action="set_value", value=enum_values[-1]),
                    "equivalence_partitioning",
                    "use another valid enum value",
                    "Alternative valid enum value can reveal hidden business restrictions.",
                )
            )

    if field.type in {"integer", "number"}:
        minimum = field.field_schema.get("minimum")
        maximum = field.field_schema.get("maximum")
        if isinstance(minimum, (int, float)):
            mutations.append(
                (
                    TestMutation(step_id=field.step_id, target=field.target, action="set_value", value=minimum - 1),
                    "boundary_value_analysis",
                    "use value below minimum",
                    "Numeric lower boundary should be tested.",
                )
            )
        if isinstance(maximum, (int, float)):
            mutations.append(
                (
                    TestMutation(step_id=field.step_id, target=field.target, action="set_value", value=maximum + 1),
                    "boundary_value_analysis",
                    "use value above maximum",
                    "Numeric upper boundary should be tested.",
                )
            )
    if field.type == "boolean" and isinstance(field.happy_value, bool):
        mutations.append(
            (
                TestMutation(step_id=field.step_id, target=field.target, action="set_value", value=not field.happy_value),
                "decision_table",
                "toggle boolean flag",
                "Boolean condition should be tested in the opposite decision branch.",
            )
        )

    if field.type == "array":
        mutations.append(
            (
                TestMutation(step_id=field.step_id, target=field.target, action="set_value", value=[]),
                "equivalence_partitioning",
                "use empty list",
                "Array field should be tested with an empty collection.",
            )
        )

    return mutations[:4]


def _priority_for_idea(idea: TestIdea) -> str:
    if idea.technique in {"business_rule_violation", "required_field_omission"}:
        return "high"
    if idea.technique in {"boundary_value_analysis", "decision_table"}:
        return "medium"
    return "low"


def _preconditions_for_case(setup_until_step: str | None) -> list[str]:
    if not setup_until_step:
        return ["Stable scenario setup starts from the first step."]
    return [f"Execute stable happy path setup through {setup_until_step}."]


def _steps_for_case(idea: TestIdea, setup_until_step: str | None) -> list[str]:
    setup = (
        f"Run stable happy path through {setup_until_step}."
        if setup_until_step
        else "Start from the first step of the stable happy path."
    )
    if idea.mutation.action == "omit_field":
        mutation = f"Execute {idea.mutation.step_id} with {idea.mutation.target} omitted."
    elif idea.mutation.action == "replace_binding_value":
        mutation = f"Execute {idea.mutation.step_id} with {idea.mutation.target} replaced by an alternate value policy."
    elif idea.mutation.action == "replace_static_data":
        mutation = f"Execute {idea.mutation.step_id} with {idea.mutation.target} replaced by tester-provided static data."
    elif idea.mutation.action == "skip_setup_step":
        mutation = (
            f"Skip setup step {idea.mutation.skipped_step_id} and execute {idea.mutation.step_id} "
            "to verify business-state validation."
        )
    elif idea.mutation.action == "repeat_step":
        mutation = f"Execute {idea.mutation.step_id} {idea.mutation.repeat_count} times."
    else:
        mutation = f"Execute {idea.mutation.step_id} with {idea.mutation.target} set to {idea.mutation.value!r}."
    return [setup, mutation]


def _expected_result_lines(expectation: TestExpectation) -> list[str]:
    lines = []
    statuses = _accepted_statuses(expectation)
    if len(statuses) > 1:
        lines.append(f"HTTP status is one of {', '.join(str(item) for item in statuses)}.")
    elif expectation.status is not None:
        lines.append(f"HTTP status is {expectation.status}.")
    if expectation.error_code:
        lines.append(f"Error code is {expectation.error_code}.")
    if expectation.description:
        lines.append(expectation.description)
    return lines or ["Expected result requires human review."]


def _rules_from_understanding(state: ProjectState, fields: list[TestDesignField]) -> list[TestDesignRule]:
    if not state.understanding:
        return []
    texts = [
        *state.understanding.preconditions,
        *state.understanding.business_rules,
        *state.understanding.success_criteria,
        *state.understanding.negative_conditions,
    ]
    rules = []
    for index, text in enumerate(texts, start=1):
        related_fields = [
            field
            for field in fields
            if _text_mentions_field(text, field.target) or _text_mentions_operation(text, field.operation.path)
        ]
        rules.append(
            TestDesignRule(
                rule_id=f"BR-{index:03d}",
                text=text,
                related_step_ids=sorted({field.step_id for field in related_fields}),
                related_targets=sorted({field.target for field in related_fields}),
                techniques=["business_rule_violation"] if related_fields else [],
                requires_human_review=True,
            )
        )
    return rules


def _schema_for_binding(operation: ApiOperation, target: str) -> dict[str, Any]:
    if target.startswith("$.path."):
        return {"type": "string"}
    if not operation.request_schema:
        return {}
    current = operation.request_schema
    for part in target.removeprefix("$.").split("."):
        if not part:
            continue
        properties = current.get("properties") if isinstance(current, dict) else None
        if not isinstance(properties, dict) or part not in properties:
            return {}
        current = properties[part]
    return current if isinstance(current, dict) else {}


def _is_required(operation: ApiOperation, target: str) -> bool:
    if target.startswith("$.path."):
        return True
    if not operation.request_schema:
        return True
    current = operation.request_schema
    parts = target.removeprefix("$.").split(".")
    for part in parts:
        if not isinstance(current, dict):
            return True
        required = set(current.get("required") or [])
        if part not in required:
            return False
        current = (current.get("properties") or {}).get(part, {})
    return True


def _schema_type(schema: dict[str, Any]) -> str:
    return schema.get("type", "unknown") if isinstance(schema, dict) else "unknown"


def _techniques_for_field(schema: dict[str, Any], location: str) -> list[str]:
    techniques = []
    schema_type = _schema_type(schema)
    if location == "body":
        techniques.append("required_field_omission")
    if schema.get("enum"):
        techniques.append("equivalence_partitioning")
    if schema_type in {"integer", "number"}:
        techniques.append("boundary_value_analysis")
    if schema_type == "boolean":
        techniques.append("decision_table")
    if schema_type == "array":
        techniques.append("equivalence_partitioning")
    return techniques


def _happy_value(trace_step, location: str, target: str) -> Any:
    if not trace_step:
        return None
    if location == "body":
        return _get_path(trace_step.request.get("body") or {}, target)
    if location == "query":
        return _get_path(trace_step.request.get("query") or {}, target)
    if location == "header":
        return _get_path(trace_step.request.get("headers") or {}, target)
    if location == "path":
        return trace_step.resolved_path
    return None


def _get_path(value: Any, target: str) -> Any:
    current = value
    for part in target.removeprefix("$.").split("."):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _negative_expectation(
    operations: list[ApiOperation],
    method: str,
    path: str,
    technique: str,
) -> TestExpectation:
    operation = next((item for item in operations if item.method == method and item.path == path), None)
    if not operation:
        return TestExpectation(status=400, description="Expected request validation failure.", source="inferred")

    validation_techniques = {
        "required_field_omission",
        "equivalence_partitioning",
        "boundary_value_analysis",
        "decision_table",
    }
    candidate_statuses = ["400", "422"] if technique in validation_techniques else ["400", "409", "422"]
    documented = [int(status) for status in candidate_statuses if status in operation.response_statuses]
    if documented:
        primary_status = documented[0]
        status_text = ", ".join(str(status) for status in documented)
        return TestExpectation(
            status=primary_status,
            accepted_statuses=documented,
            description=f"Expected documented negative HTTP status: {status_text}.",
            source="openapi",
        )
    for status in candidate_statuses:
        if status in operation.response_statuses:
            return TestExpectation(
                status=int(status),
                description=f"Expected documented negative HTTP {status}.",
                source="openapi",
            )
    return TestExpectation(
        status=None,
        description="Expected negative result requires review; no matching validation status is documented.",
        source="human_review",
    )


def _mutated_step_assertions(case: DesignedTestCase, step_id: str | None = None) -> list[TestAssertion]:
    target_step_id = step_id or case.mutated_step_id
    assertions = []
    statuses = _accepted_statuses(case.expected)
    if statuses:
        assertions.append(
            TestAssertion(
                assertion_id="A-001",
                step_id=target_step_id,
                source="expected_status",
                kind="status_in",
                expected=statuses,
                description="Mutated step must return an expected negative HTTP status.",
            )
        )
    elif case.type == "negative":
        assertions.append(
            TestAssertion(
                assertion_id="A-001",
                step_id=target_step_id,
                source="negative_mutation",
                kind="status_in",
                expected=[400, 404, 409, 422],
                description="Mutated negative step should be rejected or require explicit review.",
                confidence="medium",
                requires_human_review=True,
            )
        )
    if case.requires_human_review:
        assertions.append(
            TestAssertion(
                assertion_id=f"A-{len(assertions) + 1:03d}",
                step_id=target_step_id,
                source="human_review",
                kind="manual_review",
                description=case.expected.description or "Expected result requires human review.",
                confidence="none",
                requires_human_review=True,
            )
        )
    return assertions


def _openapi_response_assertions(
    operation: ApiOperation,
    step_id: str,
    start_index: int,
) -> list[TestAssertion]:
    schema = _success_response_schema(operation)
    if not schema:
        return []
    assertions: list[TestAssertion] = []
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for name in schema.get("required") or []:
        if name not in properties:
            continue
        json_path = f"$.{name}"
        assertions.append(
            TestAssertion(
                assertion_id=_assertion_id(start_index + len(assertions)),
                step_id=step_id,
                source="openapi_response_schema",
                kind="json_path_exists",
                json_path=json_path,
                description=f"OpenAPI success response requires {json_path}.",
            )
        )
        schema_type = properties[name].get("type") if isinstance(properties[name], dict) else None
        if schema_type:
            assertions.append(
                TestAssertion(
                    assertion_id=_assertion_id(start_index + len(assertions)),
                    step_id=step_id,
                    source="openapi_response_schema",
                    kind="json_path_type",
                    json_path=json_path,
                    expected=schema_type,
                    description=f"OpenAPI success response defines {json_path} as {schema_type}.",
                )
            )
    return assertions


def _success_response_schema(operation: ApiOperation) -> dict[str, Any]:
    for status in ["200", "201", "202", "204"]:
        schema = operation.response_schemas.get(status)
        if schema:
            return schema
    for status, schema in operation.response_schemas.items():
        if status.startswith("2"):
            return schema
    return {}


def _business_attack_to_idea(
    attack: BusinessRuleAttackIdea,
    basis: TestBasis,
    state: ProjectState,
    counter: int,
) -> TestIdea | None:
    if not _valid_step_id(attack.target_step_id, state.data_binding):
        return None

    action = _mutation_action(attack.mutation_type)
    target = attack.target or "$"
    replacement = None
    if action in {"set_value", "omit_field", "replace_binding_value", "replace_static_data"}:
        if not attack.target or not _field_exists(basis.fields, attack.target_step_id, attack.target):
            return None

    value = attack.value
    if action == "set_value":
        field = _field_by_target(basis.fields, attack.target_step_id, target)
        value = _coerce_literal_for_field(value, field)

    if action == "replace_binding_value":
        replacement = _replacement_binding_for_attack(attack, target, default_source="generated")
        if not replacement:
            return None
    elif action == "replace_static_data":
        if not attack.static_key or attack.static_key not in state.static_test_data:
            return None
        replacement = _replacement_binding_for_attack(attack, target, default_source="static")
        if not replacement:
            return None
    elif action == "skip_setup_step":
        if not attack.skipped_step_id or not _valid_skip(attack.skipped_step_id, attack.target_step_id, state.data_binding):
            return None
    elif action == "repeat_step":
        if attack.repeat_count < 2:
            return None

    return TestIdea(
        idea_id=f"TI-{counter:03d}",
        title=attack.title or f"Business rule attack for {attack.rule_id}",
        technique="business_rule_violation",
        source=attack.rule_id,
        mutation=TestMutation(
            step_id=attack.target_step_id,
            target=target,
            action=action,
            value=value,
            replacement=replacement,
            skipped_step_id=attack.skipped_step_id,
            repeat_count=max(1, attack.repeat_count),
            rationale=attack.rationale or attack.intent,
        ),
        expected=TestExpectation(
            status=None,
            description=attack.expected_behavior
            or "Expected business-rule result requires review against API behavior.",
            source="human_review",
        ),
        requires_human_review=attack.requires_human_review,
        reason=attack.rationale or attack.intent,
    )


def _mutation_action(mutation_type: str):
    return {
        "set_field_value": "set_value",
        "omit_field": "omit_field",
        "replace_binding_value": "replace_binding_value",
        "skip_setup_step": "skip_setup_step",
        "repeat_step": "repeat_step",
        "replace_static_data": "replace_static_data",
    }[mutation_type]


def _replacement_binding_for_attack(
    attack: BusinessRuleAttackIdea,
    target: str,
    default_source: str,
) -> RequestValueBinding | None:
    if default_source == "static":
        if not attack.static_key:
            return None
        return RequestValueBinding(
            target=target,
            location="body",
            source="static",
            static_key=attack.static_key,
            scope="step",
            policy="business_rule_attack_static_replacement",
            requires_human_review=True,
            reason=attack.rationale,
        )
    generator = attack.generator or "uuid"
    return RequestValueBinding(
        target=target,
        location="body",
        source="generated",
        variable=f"business_attack_{_safe_name(target)}",
        generator=generator,
        params=attack.params,
        scope="step",
        policy="business_rule_attack_generated_replacement",
        requires_human_review=True,
        reason=attack.rationale,
    )


def _execution_record_from_trace(case: DesignedTestCase, trace: ExecutorTrace) -> TestCaseExecutionRecord:
    mutated_trace = _mutated_trace_step(case, trace)
    actual_status = mutated_trace.response_status if mutated_trace else None
    status = _case_execution_status(case, trace, actual_status)
    notes = []
    if status == "failed":
        notes.append(trace.failure or "Actual execution did not match expected result.")
    if status == "contract_mismatch":
        notes.append(
            "Negative case failed at the intended step, but actual HTTP status is not documented as expected."
        )
    if status == "oracle_incomplete":
        notes.append("Business-rule attack was rejected, but no formal oracle exists yet for this rule.")
    if status == "weak_attack":
        notes.append("Business-rule attack returned 2xx; the chosen mutation may not actually violate the rule.")
    if status == "review_required":
        notes.append("Case executed, but expected result requires human review.")
    return TestCaseExecutionRecord(
        case_id=case.case_id,
        status=status,
        mode="http",
        setup_until_step=case.setup_until_step,
        mutated_step_id=case.mutated_step_id,
        expected_status=case.expected.status,
        accepted_statuses=_accepted_statuses(case.expected),
        actual_status=actual_status,
        trace=trace,
        notes=notes,
    )


def _case_execution_status(case: DesignedTestCase, trace: ExecutorTrace, actual_status: int | None) -> str:
    if case.technique == "business_rule_violation" and not _accepted_statuses(case.expected):
        expected_failure_steps = _expected_failure_step_ids(case)
        if trace.status == "passed":
            return "weak_attack"
        if trace.failed_step_id in expected_failure_steps and actual_status is not None and 400 <= actual_status < 500:
            return "oracle_incomplete"
        return "review_required"

    accepted_statuses = _accepted_statuses(case.expected)
    if not accepted_statuses:
        return "review_required"
    if case.type == "negative":
        expected_failure_steps = _expected_failure_step_ids(case)
        if trace.failed_step_id in expected_failure_steps and actual_status in accepted_statuses:
            return "passed"
        if trace.failed_step_id in expected_failure_steps and actual_status is not None and 400 <= actual_status < 500:
            return "contract_mismatch"
        return "failed"
    if trace.status == "passed":
        return "passed"
    return "failed"


def _mutated_trace_step(case: DesignedTestCase, trace: ExecutorTrace):
    expected_steps = _expected_failure_step_ids(case)
    for step_id in expected_steps:
        step = _trace_step(trace, step_id)
        if step:
            return step
    return _trace_step(trace, case.mutated_step_id)


def _set_literal_binding(binding: RequestValueBinding, value: Any) -> None:
    binding.source = "literal"
    binding.literal = value
    binding.variable = None
    binding.static_key = None
    binding.generator = None
    binding.params = {}
    binding.json_path = None
    binding.expression = None
    binding.policy = "test_case_mutation"


def _replace_binding(binding: RequestValueBinding, replacement: RequestValueBinding) -> None:
    original_target = binding.target
    original_location = binding.location
    binding.source = replacement.source
    binding.variable = replacement.variable
    binding.static_key = replacement.static_key
    binding.generator = replacement.generator
    binding.params = dict(replacement.params)
    binding.json_path = replacement.json_path
    binding.expression = replacement.expression
    binding.literal = replacement.literal
    binding.scope = replacement.scope
    binding.source_step_id = replacement.source_step_id
    binding.candidate_id = replacement.candidate_id
    binding.policy = replacement.policy or "test_case_binding_replacement"
    binding.requires_human_review = replacement.requires_human_review
    binding.reason = replacement.reason
    binding.target = original_target
    binding.location = original_location


def _build_repeat_step_plan(
    stable_plan: DataBindingPlan,
    case: DesignedTestCase,
    mutated_index: int,
) -> DataBindingPlan:
    plan = copy.deepcopy(DataBindingPlan(steps=stable_plan.steps[: mutated_index + 1]))
    step_to_repeat = copy.deepcopy(plan.steps[mutated_index])
    for _ in range(max(1, case.mutation.repeat_count) - 1):
        plan.steps.append(copy.deepcopy(step_to_repeat))
    return plan


def _build_skip_setup_plan(
    stable_plan: DataBindingPlan,
    case: DesignedTestCase,
    mutated_index: int,
) -> DataBindingPlan:
    if not case.mutation.skipped_step_id:
        raise ValueError("skip_setup_step mutation has no skipped_step_id")
    skipped_index = _step_index(case.mutation.skipped_step_id)
    if skipped_index is None or skipped_index < 0 or skipped_index >= mutated_index:
        raise ValueError(f"Invalid skipped setup step: {case.mutation.skipped_step_id}")

    original_steps = stable_plan.steps[: mutated_index + 1]
    skipped_extractions = {
        extraction.variable
        for extraction in original_steps[skipped_index].response_extractions
    }
    plan = copy.deepcopy(
        DataBindingPlan(
            steps=[
                step
                for index, step in enumerate(original_steps)
                if index != skipped_index
            ]
        )
    )
    shifted_target_index = mutated_index - 1
    if shifted_target_index < 0 or shifted_target_index >= len(plan.steps):
        raise ValueError(f"Cannot locate target step after skipping {case.mutation.skipped_step_id}")
    target_step = plan.steps[shifted_target_index]
    for binding in target_step.request_bindings:
        if binding.source == "response" and binding.variable in skipped_extractions:
            _replace_binding(
                binding,
                RequestValueBinding(
                    target=binding.target,
                    location=binding.location,
                    source="generated",
                    variable=f"skipped_{binding.variable or _safe_name(binding.target)}",
                    generator="uuid",
                    scope="step",
                    policy="skip_setup_unknown_id_replacement",
                    requires_human_review=True,
                    reason="Target step depended on a value produced by the skipped setup step.",
                ),
            )
    return plan


def _targets_equal(left: str, right: str) -> bool:
    return _normalize_target(left) == _normalize_target(right)


def _normalize_target(target: str) -> str:
    return target if target.startswith("$.") else f"$.{target}"


def _trace_step(trace: ExecutorTrace, step_id: str):
    return next((step for step in trace.steps if step.step_id == step_id), None)


def _previous_step_id(step_id: str) -> str | None:
    try:
        number = int(step_id.removeprefix("s"))
    except ValueError:
        return None
    if number <= 1:
        return None
    return f"s{number - 1:02d}"


def _step_index(step_id: str) -> int | None:
    try:
        return int(step_id.removeprefix("s")) - 1
    except ValueError:
        return None


def _expected_failure_step_ids(case: DesignedTestCase) -> set[str]:
    index = _step_index(case.mutated_step_id)
    if index is None:
        return {case.mutated_step_id}
    if case.mutation.action == "skip_setup_step" and case.mutation.skipped_step_id:
        skipped_index = _step_index(case.mutation.skipped_step_id)
        if skipped_index is not None and skipped_index < index:
            return {f"s{index:02d}"}
    if case.mutation.action == "repeat_step":
        return {
            f"s{index + offset + 1:02d}"
            for offset in range(max(1, case.mutation.repeat_count))
        }
    return {case.mutated_step_id}


def _accepted_statuses(expectation: TestExpectation) -> list[int]:
    statuses = list(dict.fromkeys(expectation.accepted_statuses))
    if expectation.status is not None and expectation.status not in statuses:
        statuses.insert(0, expectation.status)
    return statuses


def _fields_by_step(fields: list[TestDesignField]) -> dict[str, list[TestDesignField]]:
    grouped: dict[str, list[TestDesignField]] = {}
    for field in fields:
        grouped.setdefault(field.step_id, []).append(field)
    return grouped


def _field_exists(fields: list[TestDesignField], step_id: str, target: str) -> bool:
    return any(field.step_id == step_id and _targets_equal(field.target, target) for field in fields)


def _valid_step_id(step_id: str, plan: DataBindingPlan | None) -> bool:
    index = _step_index(step_id)
    return plan is not None and index is not None and 0 <= index < len(plan.steps)


def _valid_skip(skipped_step_id: str, target_step_id: str, plan: DataBindingPlan | None) -> bool:
    skipped_index = _step_index(skipped_step_id)
    target_index = _step_index(target_step_id)
    if (
        plan is None
        or skipped_index is None
        or target_index is None
        or not 0 <= skipped_index < target_index < len(plan.steps)
    ):
        return False
    skipped_method = plan.steps[skipped_index].operation.method.upper()
    return skipped_method in {"POST", "PUT", "PATCH", "DELETE"}


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_") or "value"


def _field_by_target(fields: list[TestDesignField], step_id: str, target: str) -> TestDesignField | None:
    return next((field for field in fields if field.step_id == step_id and _targets_equal(field.target, target)), None)


def _coerce_literal_for_field(value: Any, field: TestDesignField | None) -> Any:
    if field is None or not isinstance(value, str):
        return value
    if field.type == "integer" and value.strip().lstrip("-").isdigit():
        return int(value)
    if field.type == "number":
        try:
            return float(value)
        except ValueError:
            return value
    if field.type == "boolean" and value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    return value


def _business_rule_mutation_target(
    rule: TestDesignRule,
    fields_by_step: dict[str, list[TestDesignField]],
) -> tuple[str | None, str | None]:
    for step_id in reversed(rule.related_step_ids):
        fields = fields_by_step.get(step_id, [])
        for target in reversed(rule.related_targets):
            if any(_targets_equal(field.target, target) for field in fields):
                return step_id, target
        if fields:
            return step_id, fields[-1].target
    return None, None


def _text_mentions_field(text: str, target: str) -> bool:
    normalized_text = text.casefold()
    return any(token in normalized_text for token in _tokens(target) if len(token) > 3)


def _text_mentions_operation(text: str, path: str) -> bool:
    normalized_text = text.casefold()
    return any(token in normalized_text for token in _tokens(path) if len(token) > 3)


def _tokens(value: str) -> list[str]:
    return [part.casefold() for part in value.replace("{", "/").replace("}", "/").replace(".", "/").split("/") if part]


def _dedupe_assertions(assertions: list[TestAssertion]) -> list[TestAssertion]:
    seen = set()
    deduped = []
    for assertion in assertions:
        key = (assertion.step_id, assertion.kind, assertion.json_path, repr(assertion.expected))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(assertion)
    for index, assertion in enumerate(deduped, start=1):
        assertion.assertion_id = _assertion_id(index)
    return deduped


def _assertion_id(index: int) -> str:
    return f"A-{index:03d}"


def _iter_plan_steps(plan: DataBindingPlan):
    for index, step in enumerate(plan.steps, start=1):
        yield f"s{index:02d}", step


def _dedupe_ideas(ideas: list[TestIdea]) -> list[TestIdea]:
    seen = set()
    deduped = []
    for idea in ideas:
        key = (idea.technique, idea.mutation.step_id, idea.mutation.target, idea.mutation.action, repr(idea.mutation.value))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(idea)
    for index, idea in enumerate(deduped, start=1):
        idea.idea_id = f"TI-{index:03d}"
    return deduped
