from __future__ import annotations

import copy
from typing import Any

from domain import (
    ApiOperation,
    DataBindingPlan,
    DesignedTestCase,
    ExecutorTrace,
    ProjectState,
    RequestValueBinding,
    TestBasis,
    TestCaseExecutionRecord,
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
    cases = assemble_test_cases(ideas)
    executions = plan_test_case_executions(cases)
    risks = list(basis.risks)
    if not ideas:
        risks.append("No test ideas were generated from the current stable happy path.")
    return TestDesignResult(basis=basis, ideas=ideas, test_cases=cases, executions=executions, risks=risks)


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

    fields_by_step = _fields_by_step(basis.fields)
    for rule in basis.rules:
        if not rule.related_step_ids:
            continue
        step_id, target = _business_rule_mutation_target(rule, fields_by_step)
        if not step_id or not target:
            continue
        ideas.append(
            TestIdea(
                idea_id=f"TI-{counter:03d}",
                title=f"Violate business rule {rule.rule_id}",
                technique="business_rule_violation",
                source=rule.rule_id,
                mutation=TestMutation(
                    step_id=step_id,
                    target=target,
                    action="set_value",
                    value=None,
                ),
                expected=TestExpectation(
                    status=None,
                    description="Expected result must be reviewed against business rule and API behavior.",
                    source="human_review",
                ),
                requires_human_review=True,
                reason=rule.text,
            )
        )
        counter += 1

    return _dedupe_ideas(ideas)


def assemble_test_cases(ideas: list[TestIdea]) -> list[DesignedTestCase]:
    cases = []
    for index, idea in enumerate(ideas, start=1):
        setup_until_step = _previous_step_id(idea.mutation.step_id)
        cases.append(
            DesignedTestCase(
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
        )
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


def _execution_record_from_trace(case: DesignedTestCase, trace: ExecutorTrace) -> TestCaseExecutionRecord:
    mutated_trace = _trace_step(trace, case.mutated_step_id)
    actual_status = mutated_trace.response_status if mutated_trace else None
    status = _case_execution_status(case, trace, actual_status)
    notes = []
    if status == "failed":
        notes.append(trace.failure or "Actual execution did not match expected result.")
    if status == "contract_mismatch":
        notes.append(
            "Negative case failed at the intended step, but actual HTTP status is not documented as expected."
        )
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
    accepted_statuses = _accepted_statuses(case.expected)
    if not accepted_statuses:
        return "review_required"
    if case.type == "negative":
        if trace.failed_step_id == case.mutated_step_id and actual_status in accepted_statuses:
            return "passed"
        if trace.failed_step_id == case.mutated_step_id and actual_status is not None and 400 <= actual_status < 500:
            return "contract_mismatch"
        return "failed"
    if trace.status == "passed":
        return "passed"
    return "failed"


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
