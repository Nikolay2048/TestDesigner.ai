"""
Test Designer — Stage 7.

Генерирует тест-кейсы из стабилизированного happy-path по техникам тест-дизайна.

Техники:
  HAPPY_PATH  — CODE: 1 кейс на поток.
  BOUNDARY    — CODE: граничные значения из constraints (min/max/enum/length).
  NEGATIVE    — CODE: отсутствие каждого обязательного поля.
  EQUIVALENCE — LLM: семантические классы эквивалентности (1 вызов).
  NEGATIVE    — LLM: бизнес-нарушения из постановки (1 вызов).
  STATE_BASED — LLM: нарушения порядка шагов (1 вызов, только multi-step).

Принцип 3.1: boundary/missing-field — детерминированный код, LLM не нужен.
Принцип 3.2: каждая LLM-техника — отдельный вызов, строгий Pydantic-вывод.
Принцип 3.5: Python-валидация после LLM (неизвестные step_id/поля отбрасываются).
"""

import re
import uuid
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import create_llm
from src.models.flow import FlowCard, ScenarioStep, VariableBinding, VarSource
from src.models.test_design import TestCase, TestTechnique
from src.state import GraphState
from src.utils.flow_flattener import FlowCycleError, flatten_setup_chain


# ─────────────── LLM output models ─────────────────────────────────────────

class _FieldChange(BaseModel):
    field_name: str = Field(description="Name of the input field to modify")
    new_value: str = Field(description="New static value to use for this field")


class _LLMCase(BaseModel):
    title: str = Field(description="Short descriptive test case title")
    target_step_id: str = Field(description="step_id of the step being tested")
    field_changes: list[_FieldChange] = Field(
        default_factory=list,
        description="Fields to replace with new static values in the target step",
    )
    fields_to_remove: list[str] = Field(
        default_factory=list,
        description="Names of required fields to omit entirely from the request",
    )
    expected_status: int = Field(description="Expected HTTP response status code")
    reasoning: str = Field(description="Why this test case is meaningful")


class _LLMTechniqueResult(BaseModel):
    cases: list[_LLMCase] = Field(description="Generated test cases for this technique")


class _StateCase(BaseModel):
    title: str
    setup_step_ids: list[str] = Field(
        description="step_ids from the happy path to execute as setup context (in order)",
    )
    target_step_id: str = Field(description="The step to execute in the violated state")
    field_changes: list[_FieldChange] = Field(
        default_factory=list,
        description="Values to override in the target step (e.g. fake IDs when setup was skipped)",
    )
    expected_status: int
    reasoning: str


class _StateTechniqueResult(BaseModel):
    cases: list[_StateCase]


# ─────────────── Prompts ────────────────────────────────────────────────────

SYSTEM_EQUIVALENCE = """\
You are a REST API test designer creating equivalence partition test cases.

TASK: Identify equivalence classes that the JSON schema does NOT enforce.
Focus on semantic classes from the scenario text: user roles, resource states,
product categories, permission levels, incompatible value combinations.

RULES:
- Do NOT generate boundary value tests (min/max edge cases) — those are separate.
- Do NOT generate missing-field tests — those are separate.
- Do NOT repeat invalid enum values that are already obvious from the schema.
- Generate at most 6 cases total across all steps.
- Use ONLY the step_ids and field names listed in FLOW STEPS below — copy them exactly.
- Use ONLY field names that appear in the INPUTS list for the chosen step — do NOT invent fields.
- field_name in field_changes must be the bare field name exactly as shown (e.g. "cityId"),
  NOT prefixed with location (e.g. NOT "query.cityId", NOT "body.cityId").
- For POSITIVE equivalence classes (expected_status 2xx): ALL changed values MUST satisfy
  the schema constraints shown in the step (enum values, minimum/maximum, format).
  Example: if driverAge has minimum=21 and maximum=75, use 30 or 45 — never 18 or 80.
- For NEGATIVE equivalence classes (expected_status 4xx): focus on semantic violations
  that the schema does NOT already enforce (user roles, resource states, permission mismatches).
- new_value in field_changes MUST be a concrete scalar (string or number), NEVER copy the
  source description. For example: write "1499.99" not "generated:decimal_amount",
  write "RUB" not "static:RUB", write "500.00" not "default:decimal_amount".
- If no meaningful equivalence classes exist beyond the schema, return an empty list.
"""

HUMAN_EQUIVALENCE = """\
== SCENARIO TEXT ==
{scenario_text}

== FLOW STEPS (use these exact step_ids and field names) ==
{steps_text}

Generate equivalence class test cases based on semantic distinctions in the scenario text.
"""

SYSTEM_NEGATIVE = """\
You are a REST API test designer creating semantic negative test cases.

TASK: Identify business-logic violations from the scenario text:
- Non-existent or invalid referenced resources (fake/deleted IDs)
- Conflict situations (resource already used/confirmed/cancelled)
- Domain rule violations not captured in the schema (expired items, wrong owner)
- Incompatible combinations of valid values

HTTP STATUS CODE GUIDE (use these carefully):
- 404 Not Found: when referencing an ID that does not exist in the system
- 409 Conflict: when the resource exists but its STATE prevents the operation
  (e.g. already confirmed, already cancelled, already active)
- 400 Bad Request: when the request data is semantically invalid (invalid city, wrong owner)
- 422 Unprocessable Entity: semantic validation error (similar to 400)
- Do NOT use 409 for non-existent resources — use 404 for those.

RULES:
- Do NOT generate schema-level validation failures (wrong enum, out-of-range number, wrong type).
- Do NOT generate missing-field tests.
- Generate at most 6 cases total across all steps.
- For steps that receive IDs from previous steps (from_step), you CAN override them
  with fake static values (e.g. "00000000-0000-0000-0000-000000000000") to simulate non-existent resources.
- Use ONLY the step_ids and field names listed in FLOW STEPS below — copy them exactly.
- field_name in field_changes must be the bare field name exactly as shown (e.g. "carId"),
  NOT prefixed with location (e.g. NOT "body.carId", NOT "path.carId").
- new_value in field_changes MUST be a concrete scalar (string or number), NEVER copy the
  source description. For example: write "00000000-0000-0000-0000-000000000000" not
  "from_step:step_01 @ $.carId", write "INVALID_CURRENCY" not "static:USD".
- If no meaningful semantic negatives exist, return an empty list.
"""

HUMAN_NEGATIVE = """\
== SCENARIO TEXT ==
{scenario_text}

== FLOW STEPS (use these exact step_ids and field names) ==
{steps_text}

Generate semantic negative test cases that reflect business-logic violations.
"""

SYSTEM_STATE = """\
You are a REST API test designer creating state-transition test cases for a multi-step flow.

TASK: Identify meaningful state violations:
1. Skip a prerequisite step and use a fake ID for its output
   (e.g. confirm a booking without creating a draft first)
2. Repeat a step that should only happen once
   (e.g. double confirmation of the same resource)
3. Execute steps in wrong order (e.g. final step before its prerequisite)

For each case, specify:
- setup_step_ids: which happy-path steps to run first to build real context
- target_step_id: the step to run in the violated state
- field_changes: override from_step fields with fake values when setup was skipped

HTTP STATUS CODE GUIDE:
- 404 Not Found: resource with given ID does not exist
- 409 Conflict: resource exists but its state prevents the operation (already confirmed, etc.)
- 400/422 Bad Request: request is structurally or semantically invalid
- Repeating a read-only operation (GET, search) is NOT a conflict — it returns 200.
  Only use 409 when an operation mutates a resource that is already in a terminal state.

CRITICAL RULES:
- Use step_ids EXACTLY as listed in VALID STEP IDs below — short form only (e.g. "step_01").
  NEVER combine step_id with operation name (e.g. WRONG: "step_01_searchCars", RIGHT: "step_01").
- field_name in field_changes must be the bare field name (e.g. "carId"), NOT "body.carId".
- Generate at most 4 cases.
- If setup_step_ids contains a step, the target step CAN use real context from it.
- If a from_step field is NOT produced by setup_step_ids, override it with a fake value:
  - For UUID/ID fields: use "00000000-0000-0000-0000-000000000000" (valid UUID format!)
  - NEVER use strings like "fake-uuid" or "fake-car-id" — they fail UUID validation before reaching business logic.
"""

HUMAN_STATE = """\
== SCENARIO TEXT ==
{scenario_text}

== FLOW STEPS ==
{steps_text}

== VALID STEP IDs (copy these EXACTLY — short form only) ==
{step_ids_list}

Generate state-transition test cases for meaningful ordering violations.
"""


# ─────────────── Helpers ────────────────────────────────────────────────────

def _get_success_status(ep: dict) -> int:
    if "201" in ep.get("response_schemas", {}):
        return 201
    return 200


def _expand_setup_chain(
    requested_ids: list[str],
    flow_card: FlowCard,
    step_map: dict[str, ScenarioStep],
) -> list[ScenarioStep]:
    """
    Ensures all FROM_STEP dependencies of requested setup steps are included.

    If the LLM said setup=[step_02, step_03] but step_02 needs step_01 via FROM_STEP,
    this function adds step_01 before step_02, preserving flow_card order.
    """
    needed: set[str] = set(requested_ids)
    changed = True
    while changed:
        changed = False
        for sid in list(needed):
            step = step_map.get(sid)
            if step is None:
                continue
            for b in step.inputs:
                if b.source == VarSource.FROM_STEP and b.source_ref:
                    if b.source_ref not in needed:
                        needed.add(b.source_ref)
                        changed = True

    # Return steps in flow_card order
    return [s for s in flow_card.steps if s.step_id in needed]


def _get_error_status(ep: dict) -> int:
    """Returns the expected validation error status from the spec (400 preferred over 422)."""
    schemas = ep.get("response_schemas", {})
    for code in ("400", "422"):
        if code in schemas:
            return int(code)
    return 400


def _preceding_steps(flow_card: FlowCard, target_step_id: str) -> list[ScenarioStep]:
    """Returns all steps before target_step_id in the flow."""
    result = []
    for step in flow_card.steps:
        if step.step_id == target_step_id:
            break
        result.append(step)
    return result


def _any_mutation_applies(
    step: ScenarioStep,
    changes: dict[str, str],
    to_remove: set[str],
    allow_context_override: bool,
) -> bool:
    """True if at least one mutation from changes/to_remove will actually be written to inputs."""
    if to_remove:
        return True
    for name in changes:
        binding = next((b for b in step.inputs if b.name == name), None)
        if binding is None:
            continue
        protected = binding.source in (VarSource.FROM_STEP, VarSource.FROM_FLOW, VarSource.ENV)
        if not protected or allow_context_override:
            return True
    return False


def _apply_mutations(
    step: ScenarioStep,
    changes: dict[str, str],      # field_name -> new_value
    to_remove: set[str],
    allow_context_override: bool = False,
) -> list[VariableBinding]:
    """
    Returns a new inputs list with mutations applied.
    By default, from_step/env/from_flow fields are NOT overridden (they come from context).
    Set allow_context_override=True for state_based/negative tests that use fake IDs.
    """
    result = []
    for binding in step.inputs:
        if binding.name in to_remove:
            continue
        if binding.name in changes:
            protected = binding.source in (VarSource.FROM_STEP, VarSource.FROM_FLOW, VarSource.ENV)
            if protected and not allow_context_override:
                result.append(binding)
            else:
                result.append(binding.model_copy(update={
                    "source": VarSource.STATIC,
                    "value": changes[binding.name],
                    "generator": None,
                    "source_ref": None,
                    "source_field": None,
                }))
        else:
            result.append(binding)
    return result


def _make_case(
    flow_id: str,
    step: ScenarioStep,
    technique: TestTechnique,
    title: str,
    setup: list[ScenarioStep],
    modified_inputs: list[VariableBinding],
    expected_status: int,
    assertions: list[dict] | None = None,
) -> TestCase:
    if assertions is None:
        # For error cases accept any 4xx — both 400 and 422 mean "validation rejected".
        # This avoids false failures when FastAPI type-checks before business logic.
        if 400 <= expected_status < 500:
            assertions = [{"type": "status_code", "expected_range": [400, 499]}]
        else:
            assertions = [{"type": "status_code", "expected": expected_status}]
    return TestCase(
        case_id=f"{flow_id}_{technique.value}_{uuid.uuid4().hex[:8]}",
        flow_id=flow_id,
        technique=technique,
        title=title,
        target_step=step.step_id,
        setup_chain=setup,
        modified_inputs=modified_inputs,
        expected_status=expected_status,
        assertions=assertions,
        group=f"{flow_id}/{step.operation_id}/{technique.value}",
    )


_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)
_SOURCE_DESCRIPTOR_RE = re.compile(
    r'^(generated|static|from_step|env|default|auto)\s*[=:]',
    re.IGNORECASE,
)
_UUID_FIELD_SUFFIXES = ("id", "Id", "ID", "uuid", "UUID", "Uuid")


def _is_valid_uuid_format(val: str) -> bool:
    return bool(_UUID_RE.match(val))


def _looks_like_uuid_field(binding: VariableBinding) -> bool:
    """True if the binding's name/generator suggests it holds a UUID."""
    name = binding.name or ""
    gen = binding.generator or ""
    return (
        any(name.endswith(s) for s in _UUID_FIELD_SUFFIXES)
        or gen == "uuid4"
        or (binding.source == VarSource.FROM_STEP and any(name.endswith(s) for s in ("Id", "ID", "id")))
    )


def _is_source_descriptor(value: str) -> bool:
    """True if the LLM accidentally returned a source-description string
    (e.g. 'default:decimal_amount', 'static:RUB') instead of a concrete value."""
    return bool(_SOURCE_DESCRIPTOR_RE.match(value))


_GENERATOR_EXAMPLES: dict[str, str] = {
    "uuid4": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "future_datetime": "2026-06-01T10:00:00",
    "future_datetime_start": "2026-06-01T10:00:00",
    "future_datetime_end": "2026-06-02T10:00:00",
    "decimal_amount": "1499.99",
    "fake_email": "user_test@example.com",
    "random_string": "test_abc1234567",
}


def _example_for_generator(gen: str) -> str:
    return _GENERATOR_EXAMPLES.get(gen, "...")


def _fake_value_for_binding(binding: VariableBinding) -> str:
    """Returns an appropriate fake value for a skipped FROM_STEP binding."""
    gen = binding.generator or ""
    if gen in ("future_datetime", "future_datetime_start", "future_datetime_end"):
        return "2025-01-01T10:00:00"
    if gen == "decimal_amount":
        return "0.00"
    if gen == "uuid4" or _looks_like_uuid_field(binding):
        return "00000000-0000-0000-0000-000000000000"
    return "00000000-0000-0000-0000-000000000000"


def _invalid_enum_value(enum_vals: list) -> str:
    """Returns a value guaranteed not to be in the enum list.

    For numeric enums returns a numeric string (avoids FastAPI 422 type-error
    before business logic runs; the business logic then rejects the value with 400).
    """
    all_numeric = all(isinstance(v, (int, float)) for v in enum_vals)
    if all_numeric:
        occupied = {int(v) for v in enum_vals}
        for candidate in (999, 0, -1, 10000, 99999):
            if candidate not in occupied:
                return str(candidate)
    str_vals = {str(v) for v in enum_vals}
    for candidate in ("INVALID", "999", "0", "NONE", "__invalid__"):
        if candidate not in str_vals:
            return candidate
    return f"__invalid_{len(enum_vals)}__"


def _steps_text(flow_card: FlowCard, ep_map: dict) -> str:
    """Compact human-readable summary of all steps for LLM prompts."""
    lines = []
    for step in flow_card.steps:
        ep = ep_map.get(step.operation_id, {})
        lines.append(f"Step {step.step_id}: {step.operation_id}")
        lines.append(f"  {ep.get('method', '?')} {ep.get('path', '?')}")
        lines.append("  Inputs:")
        for b in step.inputs:
            if b.source == VarSource.STATIC:
                src_desc = f'current value: "{b.value}"'
            elif b.source == VarSource.GENERATED:
                example = _example_for_generator(b.generator or "uuid4")
                src_desc = f'auto-generated (example: "{example}")'
            elif b.source == VarSource.FROM_STEP:
                src_desc = f"[DO NOT CHANGE: comes from {b.source_ref}]"
            elif b.source == VarSource.ENV:
                src_desc = f"[DO NOT CHANGE: env variable]"
            else:
                src_desc = str(b.source)
            c = ep.get("constraints", {}).get(b.name, {})
            if "enum" in c:
                constraint = f"  [allowed: {c['enum']}]"
            elif "minimum" in c or "maximum" in c:
                parts = []
                if "minimum" in c:
                    parts.append(f"min={c['minimum']}")
                if "maximum" in c:
                    parts.append(f"max={c['maximum']}")
                constraint = f"  [{', '.join(parts)}]"
            elif "maxLength" in c:
                constraint = f"  [maxLength={c['maxLength']}]"
            else:
                constraint = ""
            lines.append(f"    {b.name} ({b.target_location}): {src_desc}{constraint}")
        req_qp = [qp["name"] for qp in ep.get("query_params", []) if qp.get("required")]
        req_body = ep.get("required_fields", [])
        if req_qp or req_body:
            lines.append(f"  Required: {req_qp + req_body}")
        if step.produces:
            lines.append(f"  Produces: {step.produces}")
        lines.append("")
    return "\n".join(lines)


# ─────────────── CODE-based generators ──────────────────────────────────────

def _generate_happy_path(flow_card: FlowCard, ep_map: dict) -> list[TestCase]:
    if not flow_card.steps:
        return []
    last = flow_card.steps[-1]
    ep = ep_map.get(last.operation_id, {})
    success_status = _get_success_status(ep)
    return [TestCase(
        case_id=f"{flow_card.flow_id}_happy_path",
        flow_id=flow_card.flow_id,
        technique=TestTechnique.HAPPY_PATH,
        title=f"Happy path: {flow_card.name}",
        target_step=last.step_id,
        setup_chain=list(flow_card.steps[:-1]),
        modified_inputs=list(last.inputs),
        expected_status=success_status,
        assertions=[{"type": "status_code", "expected_range": [200, 299]}],
        group=f"{flow_card.flow_id}/{last.operation_id}/happy_path",
    )]


def _generate_boundary(flow_card: FlowCard, ep_map: dict) -> list[TestCase]:
    cases = []
    for step in flow_card.steps:
        ep = ep_map.get(step.operation_id, {})
        constraints = ep.get("constraints", {})
        if not constraints:
            continue
        success_status = _get_success_status(ep)
        error_status = _get_error_status(ep)  # 400 or 422, from spec
        setup = _preceding_steps(flow_card, step.step_id)

        for binding in step.inputs:
            if binding.source not in (VarSource.STATIC, VarSource.GENERATED):
                continue  # only test controllable inputs
            c = constraints.get(binding.name)
            if not c:
                continue

            # enum: test one value not in enum (invalid)
            if "enum" in c:
                invalid = _invalid_enum_value(c["enum"])
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} not in enum ({invalid})",
                    setup,
                    _apply_mutations(step, {binding.name: invalid}, set()),
                    error_status,
                ))

            # minimum: just below (invalid) and at minimum (valid)
            if "minimum" in c:
                mn = c["minimum"]
                below = str(mn - 1)
                at_min = str(mn)
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} below minimum ({below} < {mn})",
                    setup,
                    _apply_mutations(step, {binding.name: below}, set()),
                    error_status,
                ))
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} at minimum ({at_min})",
                    setup,
                    _apply_mutations(step, {binding.name: at_min}, set()),
                    success_status,
                ))

            # maximum: at maximum (valid) and just above (invalid)
            if "maximum" in c:
                mx = c["maximum"]
                at_max = str(mx)
                above = str(mx + 1)
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} at maximum ({at_max})",
                    setup,
                    _apply_mutations(step, {binding.name: at_max}, set()),
                    success_status,
                ))
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} above maximum ({above} > {mx})",
                    setup,
                    _apply_mutations(step, {binding.name: above}, set()),
                    error_status,
                ))

            # maxLength: at limit (valid) and over limit (invalid)
            if "maxLength" in c:
                ml = c["maxLength"]
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} at maxLength ({ml} chars)",
                    setup,
                    _apply_mutations(step, {binding.name: "x" * ml}, set()),
                    success_status,
                ))
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} over maxLength ({ml + 1} chars)",
                    setup,
                    _apply_mutations(step, {binding.name: "x" * (ml + 1)}, set()),
                    error_status,
                ))

            # minLength: at limit (valid) and below limit (invalid)
            if "minLength" in c:
                ml = c["minLength"]
                at_ml = "x" * ml
                below_ml = "x" * max(0, ml - 1)
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} at minLength ({ml} chars)",
                    setup,
                    _apply_mutations(step, {binding.name: at_ml}, set()),
                    success_status,
                ))
                cases.append(_make_case(
                    flow_card.flow_id, step, TestTechnique.BOUNDARY,
                    f"{step.operation_id}: {binding.name} below minLength ({len(below_ml)} chars)",
                    setup,
                    _apply_mutations(step, {binding.name: below_ml}, set()),
                    error_status,
                ))
    return cases


def _generate_missing_field(flow_card: FlowCard, ep_map: dict) -> list[TestCase]:
    cases = []
    for step in flow_card.steps:
        ep = ep_map.get(step.operation_id, {})
        error_status = _get_error_status(ep)
        setup = _preceding_steps(flow_card, step.step_id)

        required: set[str] = set()
        for qp in ep.get("query_params", []):
            if qp.get("required"):
                required.add(qp["name"])
        required.update(ep.get("required_fields", []))

        for binding in step.inputs:
            if binding.name not in required:
                continue
            if binding.source in (VarSource.FROM_STEP, VarSource.FROM_FLOW, VarSource.ENV):
                continue  # resolved from context, not from test data
            remaining = [b for b in step.inputs if b.name != binding.name]
            cases.append(_make_case(
                flow_card.flow_id, step, TestTechnique.NEGATIVE,
                f"{step.operation_id}: missing required field '{binding.name}'",
                setup, remaining, error_status,
            ))
    return cases


# ─────────────── LLM infrastructure ─────────────────────────────────────────

def _call_llm(system: str, human: str, output_model: type, variables: dict) -> Any | None:
    llm = create_llm()
    chain = (
        ChatPromptTemplate.from_messages([("system", system), ("human", human)])
        | llm.with_structured_output(output_model)
    )
    try:
        return chain.invoke(variables)
    except Exception as e:
        print(f"[test_designer] LLM error: {e}")
        return None


def _cases_from_llm_result(
    result: _LLMTechniqueResult | None,
    technique: TestTechnique,
    flow_card: FlowCard,
    ep_map: dict,
    step_map: dict[str, ScenarioStep],
    allow_context_override: bool = False,
) -> list[TestCase]:
    if not result:
        return []
    valid_step_ids = set(step_map)
    cases = []
    for llm_case in result.cases:
        if llm_case.target_step_id not in valid_step_ids:
            print(f"[test_designer] unknown step_id {llm_case.target_step_id!r} from LLM, skipping")
            continue
        step = step_map[llm_case.target_step_id]
        input_names = {b.name for b in step.inputs}

        changes = {}
        for fc in llm_case.field_changes:
            # Strip location prefix if LLM added it (e.g. "query.cityId" → "cityId")
            field_name = fc.field_name
            if "." in field_name and field_name not in input_names:
                field_name = field_name.split(".")[-1]
            if field_name not in input_names:
                print(f"[test_designer] unknown field {fc.field_name!r} in LLM case, skipping")
                continue
            val = fc.new_value
            if _is_source_descriptor(val):
                print(f"[test_designer] dropping source-descriptor value {val!r} for field {field_name!r}")
                continue
            changes[field_name] = val

        # Replace non-UUID strings only in fields that genuinely hold UUIDs:
        # from_step/from_flow bindings (IDs propagated from other steps) or uuid4-generated fields.
        # STATIC and numeric-enum fields (e.g. cityId=36) are intentionally excluded.
        for b in step.inputs:
            if b.name not in changes:
                continue
            is_uuid_field = (
                b.source in (VarSource.FROM_STEP, VarSource.FROM_FLOW)
                or b.generator == "uuid4"
            )
            if is_uuid_field and not _is_valid_uuid_format(changes[b.name]):
                changes[b.name] = "00000000-0000-0000-0000-000000000000"

        to_remove = set()
        for f in llm_case.fields_to_remove:
            name = f if f in input_names else (f.split(".")[-1] if "." in f else f)
            if name in input_names:
                to_remove.add(name)

        if not _any_mutation_applies(step, changes, to_remove, allow_context_override):
            print(f"[test_designer] skipping '{llm_case.title}': no mutations apply "
                  f"(unknown or protected fields)")
            continue

        modified = _apply_mutations(step, changes, to_remove, allow_context_override)
        setup = _preceding_steps(flow_card, step.step_id)
        cases.append(_make_case(
            flow_card.flow_id, step, technique,
            llm_case.title, setup, modified, llm_case.expected_status,
        ))
    return cases


# ─────────────── LLM-based generators ───────────────────────────────────────

def _generate_equivalence_llm(
    flow_card: FlowCard,
    ep_map: dict,
    raw_scenarios: str,
    step_map: dict[str, ScenarioStep],
) -> list[TestCase]:
    result = _call_llm(
        SYSTEM_EQUIVALENCE, HUMAN_EQUIVALENCE, _LLMTechniqueResult,
        {"scenario_text": raw_scenarios[:3000], "steps_text": _steps_text(flow_card, ep_map)},
    )
    return _cases_from_llm_result(result, TestTechnique.EQUIVALENCE, flow_card, ep_map, step_map)


def _generate_negative_llm(
    flow_card: FlowCard,
    ep_map: dict,
    raw_scenarios: str,
    step_map: dict[str, ScenarioStep],
) -> list[TestCase]:
    result = _call_llm(
        SYSTEM_NEGATIVE, HUMAN_NEGATIVE, _LLMTechniqueResult,
        {"scenario_text": raw_scenarios[:3000], "steps_text": _steps_text(flow_card, ep_map)},
    )
    return _cases_from_llm_result(
        result, TestTechnique.NEGATIVE, flow_card, ep_map, step_map,
        allow_context_override=True,  # semantic negatives may need fake IDs for from_step fields
    )


def _generate_state_based_llm(
    flow_card: FlowCard,
    ep_map: dict,
    raw_scenarios: str,
    step_map: dict[str, ScenarioStep],
) -> list[TestCase]:
    if len(flow_card.steps) < 2:
        return []

    step_ids_list = ", ".join(sorted(step_map.keys()))
    result = _call_llm(
        SYSTEM_STATE, HUMAN_STATE, _StateTechniqueResult,
        {
            "scenario_text": raw_scenarios[:3000],
            "steps_text": _steps_text(flow_card, ep_map),
            "step_ids_list": step_ids_list,
        },
    )
    if not result:
        return []

    valid_step_ids = set(step_map)
    cases = []
    for sc in result.cases:
        # Attempt to normalise IDs that LLM may have appended with operation name
        target = sc.target_step_id
        if target not in valid_step_ids:
            # try stripping suffix after first underscore-separated part
            for sid in valid_step_ids:
                if target.startswith(sid):
                    target = sid
                    break
        if target not in valid_step_ids:
            print(f"[test_designer] state: unknown target {sc.target_step_id!r}, skipping")
            continue
        sc.target_step_id = target

        setup_ids = []
        for sid in sc.setup_step_ids:
            if sid in valid_step_ids:
                setup_ids.append(sid)
            else:
                normalised = next((s for s in valid_step_ids if sid.startswith(s)), None)
                if normalised:
                    setup_ids.append(normalised)
                else:
                    print(f"[test_designer] state: unknown setup step {sid!r}, skipping case")
                    setup_ids = None
                    break
        if setup_ids is None:
            continue
        sc.setup_step_ids = setup_ids

        target_step = step_map[sc.target_step_id]
        input_names = {b.name for b in target_step.inputs}
        changes = {fc.field_name: fc.new_value for fc in sc.field_changes if fc.field_name in input_names}

        # Auto-fix: FROM_STEP bindings that reference skipped steps → fake STATIC value.
        # Without this, the executor can't resolve those fields and raises resolve_error.
        setup_id_set = set(sc.setup_step_ids)
        for b in target_step.inputs:
            if b.source == VarSource.FROM_STEP and b.source_ref not in setup_id_set:
                if b.name not in changes:
                    changes[b.name] = _fake_value_for_binding(b)

        # Validate LLM field_changes: replace non-UUID strings in UUID-typed fields.
        for b in target_step.inputs:
            if b.name in changes:
                raw_val = changes[b.name]
                if _looks_like_uuid_field(b) and not _is_valid_uuid_format(raw_val):
                    changes[b.name] = "00000000-0000-0000-0000-000000000000"

        modified = _apply_mutations(target_step, changes, set(), allow_context_override=True)

        setup_steps = _expand_setup_chain(sc.setup_step_ids, flow_card, step_map)
        exp = sc.expected_status
        assertions = (
            [{"type": "status_code", "expected_range": [400, 499]}]
            if 400 <= exp < 500
            else [{"type": "status_code", "expected": exp}]
        )
        cases.append(TestCase(
            case_id=f"{flow_card.flow_id}_state_{uuid.uuid4().hex[:8]}",
            flow_id=flow_card.flow_id,
            technique=TestTechnique.STATE_BASED,
            title=sc.title,
            target_step=sc.target_step_id,
            setup_chain=setup_steps,
            modified_inputs=modified,
            expected_status=exp,
            assertions=assertions,
            group=f"{flow_card.flow_id}/{target_step.operation_id}/state_based",
        ))
    return cases


# ─────────────── Main node ──────────────────────────────────────────────────

def test_designer(state: GraphState) -> dict:
    stabilized_dict = state.get("stabilized_card", {})
    if not stabilized_dict or not stabilized_dict.get("is_stabilized"):
        print("[test_designer] no stabilized card — skipping")
        return {"test_cases": [], "trace": ["test_designer"]}

    try:
        flow_card = FlowCard(**stabilized_dict)
    except Exception as e:
        print(f"[test_designer] invalid flow_card: {e}")
        return {"test_cases": [], "trace": ["test_designer"]}

    endpoints: list[dict] = state.get("endpoints", [])
    raw_scenarios: str = state.get("raw_scenarios", "")

    ep_map = {ep["operation_id"]: ep for ep in endpoints}
    step_map = {step.step_id: step for step in flow_card.steps}

    # Inter-flow setup (Stage 8): prepended to setup_chain of every test case.
    # all_stabilized_cards accumulates across executor runs (Annotated[list, add]).
    inter_flow_setup: list[ScenarioStep] = []
    if flow_card.requires_flows:
        all_cards_raw: list[dict] = state.get("all_stabilized_cards", [])
        all_flows = {d["flow_id"]: FlowCard(**d) for d in all_cards_raw if d.get("flow_id")}
        all_flows[flow_card.flow_id] = flow_card  # include self for cycle detection
        try:
            inter_flow_setup = flatten_setup_chain(flow_card, all_flows)
            print(f"[test_designer] inter-flow setup: {[s.step_id for s in inter_flow_setup]}")
        except (FlowCycleError, KeyError) as e:
            print(f"[test_designer] inter-flow error: {e}")

    all_cases: list[TestCase] = []

    # Deterministic techniques — cheap, no LLM
    all_cases.extend(_generate_happy_path(flow_card, ep_map))
    all_cases.extend(_generate_boundary(flow_card, ep_map))
    all_cases.extend(_generate_missing_field(flow_card, ep_map))

    # LLM-based techniques — one call each
    all_cases.extend(_generate_equivalence_llm(flow_card, ep_map, raw_scenarios, step_map))
    all_cases.extend(_generate_negative_llm(flow_card, ep_map, raw_scenarios, step_map))
    all_cases.extend(_generate_state_based_llm(flow_card, ep_map, raw_scenarios, step_map))

    # Prepend inter-flow setup to every case's setup_chain
    if inter_flow_setup:
        all_cases = [
            tc.model_copy(update={"setup_chain": inter_flow_setup + tc.setup_chain})
            for tc in all_cases
        ]

    by_technique: dict[str, int] = {}
    for tc in all_cases:
        by_technique[tc.technique.value] = by_technique.get(tc.technique.value, 0) + 1
    print(f"[test_designer] {len(all_cases)} test cases: " + ", ".join(
        f"{t}={n}" for t, n in sorted(by_technique.items())
    ))

    return {
        "test_cases": [tc.model_dump(mode="json") for tc in all_cases],
        "trace": ["test_designer"],
    }
