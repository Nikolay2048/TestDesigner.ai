from __future__ import annotations

import json

from pydantic import ValidationError

from domain import (
    AgentMessage,
    AgentRun,
    BusinessRuleAttackIdea,
    ProjectState,
    TestDesignResult,
    TestIdeaRefinementResult,
)
from llm import LLM, NoLLM, extract_json
from test_design import append_business_rule_attack_ideas, build_test_design


class TestDesignerAgent:
    """Builds reviewable test cases as mutations of the stable happy path."""

    __test__ = False
    name = "Test Designer"

    def __init__(self, llm: LLM | None = None):
        self.llm = llm or NoLLM()

    def run(self, state: ProjectState) -> tuple[ProjectState, AgentRun]:
        if not state.stabilization or state.stabilization.status != "passed":
            return state, AgentRun(
                agent_name=self.name,
                status="stub",
                notes=["Stable happy path is required before test design."],
            )

        state.test_design = build_test_design(state)
        business_prompts = _build_business_attack_prompts(state)
        business_notes = _add_business_attacks_with_llm(state, self.llm, business_prompts)
        prompt = _build_refinement_prompt(state)
        notes = _refine_with_llm(state.test_design, self.llm, prompt)
        if state.test_design.test_cases:
            state.test_design.risks = [risk for risk in state.test_design.risks if risk != "No test ideas were generated from the current stable happy path."]
        else:
            notes.append("Test design failed: no executable test cases were generated.")
        return state, AgentRun(
            agent_name=self.name,
            status="completed" if state.test_design.test_cases else "failed",
            prompt=[message for business_prompt in business_prompts for message in business_prompt] + prompt,
            output=state.test_design.model_dump(mode="json"),
            notes=[
                f"Fields analyzed: {len(state.test_design.basis.fields)}.",
                f"Test ideas generated: {len(state.test_design.ideas)}.",
                f"Test cases assembled: {len(state.test_design.test_cases)}.",
                *business_notes,
                *notes,
            ],
        )


def _build_business_attack_prompts(state: ProjectState) -> list[list[AgentMessage]]:
    if not state.test_design:
        return []
    rules = [
        rule
        for rule in state.test_design.basis.rules
        if rule.text
    ][:8]
    if not rules:
        return []

    steps = []
    if state.data_binding:
        for index, step in enumerate(state.data_binding.steps, start=1):
            bindings = [
                {
                    "target": binding.target,
                    "location": binding.location,
                    "source": binding.source,
                    "type": _field_type(state, f"s{index:02d}", binding.target),
                    "happy_value": _field_happy_value(state, f"s{index:02d}", binding.target),
                }
                for binding in step.request_bindings
            ]
            steps.append(
                {
                    "step_id": f"s{index:02d}",
                    "business_step": step.business_step,
                    "operation": step.operation.model_dump(mode="json"),
                    "request_bindings": bindings,
                }
            )

    prompts = []
    for rule in rules:
        related_fields = _related_or_all_fields(state, rule.rule_id)
        prompts.append(
            [
                AgentMessage(
                    role="system",
                    content=(
                        "You propose business-rule test attacks for a stable REST happy path. "
                        "Be creative about business intent, but return only attacks that can be mapped to the allowed mutation types. "
                        "Return strict JSON only."
                    ),
                ),
                AgentMessage(
                    role="user",
                    content=f"""
Business rule:
{json.dumps(rule.model_dump(mode="json"), ensure_ascii=False, indent=2)}

Scenario context (requirements and preconditions):
{state.scenario.text}

Available tester static keys:
{json.dumps(list(state.static_test_data), ensure_ascii=False)}

Stable REST steps:
{json.dumps(steps, ensure_ascii=False, indent=2)}

Candidate fields with happy values:
{json.dumps(related_fields, ensure_ascii=False, indent=2)}

Allowed mutation_type values:
- set_field_value: change an existing request field value.
- omit_field: remove an existing request field.
- replace_binding_value: replace an existing request field/path value with generated uuid or another generated value.
- skip_setup_step: skip an earlier setup step before executing the target step.
- repeat_step: execute the same target step more than once.
- replace_static_data: replace an existing request field with a tester-provided static key.

Return JSON with this shape:
{{
  "ideas": [
    {{
      "rule_id": "BR-001",
      "title": "TMS-friendly business test title",
      "intent": "what business rule is being attacked",
      "mutation_type": "set_field_value",
      "target_step_id": "s07",
      "target": "$.fieldName or $.path.id; null only for repeat_step/skip_setup_step",
      "value": "new literal value for set_field_value, otherwise null",
      "skipped_step_id": "s04 or null",
      "repeat_count": 2,
      "static_key": "key from tester constants or null",
      "generator": "uuid or null",
      "params": {{}},
      "expected_behavior": "business-level expected result; do not invent exact status unless documented",
      "rationale": "why this attack validates the rule",
      "confidence": "high|medium|low|none",
      "requires_human_review": true
    }}
  ],
  "risks": ["risk"]
}}

Rules:
- Use only step_id and target values present in Stable REST steps.
- Use JSON values with the correct type: numbers as numbers, booleans as booleans, not strings.
- Generate 2-4 distinct attacks for this one business rule when possible.
- Prefer fewer meaningful tests to filling a quota. Return no ideas and explain the coverage gap in risks if this rule cannot be tested with the available mutations.
- A random or malformed identifier tests lookup or validation, not an unpaid, cancelled, or differently owned resource. Do not claim it tests those business states.
- Each attack must actually establish the precondition needed to exercise its rule. Do not invent fixture identifiers, static keys, or setup steps.
- Repeat operations only to test a documented transition or idempotency requirement; do not assume every repeat must fail.
- For replace_binding_value use generator="uuid" unless another listed generator is clearly better.
- For repeat_step use repeat_count >= 2.
- For all other mutation types use repeat_count=1; never return null.
- Use skip_setup_step only for skipping state-changing setup operations such as create/confirm/start/pay.
- Do not use skip_setup_step to skip lookup/search/list operations; use set_field_value or replace_binding_value instead.
- If the rule is about two existing values being different, mutate one of those request fields directly.
- Prefer business-value and business-state attacks over schema-only checks.
- Do not create schema-validation duplicates when a business-state attack is possible.
""".strip(),
                ),
            ]
        )
    return prompts


def _add_business_attacks_with_llm(
    state: ProjectState,
    llm: LLM,
    prompts: list[list[AgentMessage]],
) -> list[str]:
    if not prompts or not state.test_design:
        return ["Business-rule attack generation skipped: no business rules found."]

    total_proposed = 0
    total_added = 0
    notes = []
    for index, prompt in enumerate(prompts, start=1):
        try:
            raw = llm.complete(prompt)
        except RuntimeError as exc:
            if index == 1:
                return [f"Business-rule attack generation skipped: {exc}"]
            notes.append(f"Business-rule attack batch {index} skipped: {exc}")
            continue

        try:
            parsed = extract_json(raw)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("ideas"), list):
                raise ValueError("Expected an object with an ideas array")
        except (ValueError, ValidationError) as exc:
            state.test_design.risks.append(f"LLM business-rule attack batch {index} ignored: {exc}")
            notes.append(f"Business-rule attack batch {index} ignored because output did not match schema.")
            continue

        total_proposed += len(parsed["ideas"])
        attacks = []
        for idea_index, idea in enumerate(parsed["ideas"], start=1):
            try:
                attacks.append(BusinessRuleAttackIdea.model_validate(idea))
            except (ValueError, ValidationError) as exc:
                diagnostic = f"LLM business-rule attack batch {index}, idea {idea_index} ignored: {exc}"
                state.test_design.risks.append(diagnostic)
                notes.append(diagnostic)
        risks = parsed.get("risks", [])
        if isinstance(risks, list):
            state.test_design.risks.extend(risk for risk in risks if isinstance(risk, str))
        before = len(state.test_design.ideas)
        batch_notes = append_business_rule_attack_ideas(state.test_design, attacks, state)
        after = len(state.test_design.ideas)
        total_added += max(0, after - before)
        notes.extend(note for note in batch_notes if not note.startswith("Business-rule executable attacks accepted:"))
    return [
        f"LLM business-rule attack batches: {len(prompts)}.",
        f"LLM business-rule attacks proposed: {total_proposed}.",
        f"Business-rule executable attacks added after deduplication: {total_added}.",
        *notes,
    ]


def _field_type(state: ProjectState, step_id: str, target: str) -> str:
    if not state.test_design:
        return "unknown"
    field = next(
        (
            item
            for item in state.test_design.basis.fields
            if item.step_id == step_id and item.target == target
        ),
        None,
    )
    return field.type if field else "unknown"


def _field_happy_value(state: ProjectState, step_id: str, target: str):
    if not state.test_design:
        return None
    field = next(
        (
            item
            for item in state.test_design.basis.fields
            if item.step_id == step_id and item.target == target
        ),
        None,
    )
    return field.happy_value if field else None


def _related_or_all_fields(state: ProjectState, rule_id: str) -> list[dict]:
    if not state.test_design:
        return []
    rule = next((item for item in state.test_design.basis.rules if item.rule_id == rule_id), None)
    related_keys = {
        (step_id, target)
        for step_id in (rule.related_step_ids if rule else [])
        for target in (rule.related_targets if rule else [])
    }
    fields = [
        field
        for field in state.test_design.basis.fields
        if not related_keys or (field.step_id, field.target) in related_keys
    ]
    return [
        {
            "step_id": field.step_id,
            "business_step": field.business_step,
            "operation": field.operation.model_dump(mode="json"),
            "target": field.target,
            "location": field.location,
            "type": field.type,
            "required": field.required,
            "happy_value": field.happy_value,
            "binding_source": field.binding_source,
        }
        for field in fields[:30]
    ]


def _build_refinement_prompt(state: ProjectState) -> list[AgentMessage]:
    ideas = state.test_design.ideas[:8] if state.test_design else []
    compact_ideas = [
        {
            "idea_id": idea.idea_id,
            "title": idea.title,
            "technique": idea.technique,
            "source": idea.source,
            "mutation": idea.mutation.model_dump(mode="json"),
            "expected": idea.expected.model_dump(mode="json"),
            "reason": idea.reason,
        }
        for idea in ideas
    ]
    business_context = []
    if state.understanding:
        business_context = [
            *state.understanding.business_rules,
            *state.understanding.success_criteria,
            *state.understanding.negative_conditions,
        ][:10]
    return [
        AgentMessage(
            role="system",
            content=(
                "You refine test design wording for TMS import. "
                "Do not change test IDs, techniques, mutations, targets, or expected HTTP statuses. "
                "Return strict JSON only."
            ),
        ),
        AgentMessage(
            role="user",
            content=f"""
Business context:
{json.dumps(business_context, ensure_ascii=False, indent=2)}

Deterministic test ideas:
{json.dumps(compact_ideas, ensure_ascii=False, indent=2)}

Return JSON with this shape:
{{
  "refinements": [
    {{
      "idea_id": "TI-001",
      "title": "clear TMS-friendly title, or null",
      "reason": "short rationale tied to the technique, or null",
      "expected_description": "clear expected result wording, or null",
      "requires_human_review": true
    }}
  ],
  "risks": ["risk"]
}}

Rules:
- Refine wording only.
- Do not add new ideas.
- Do not remove ideas.
- Do not change mutation values.
- Do not change expected statuses.
- Use requires_human_review=true when the expected behavior is inferred or ambiguous.
""".strip(),
        ),
    ]


def _refine_with_llm(test_design: TestDesignResult, llm: LLM, prompt: list[AgentMessage]) -> list[str]:
    try:
        raw = llm.complete(prompt)
    except RuntimeError as exc:
        return [f"LLM refinement skipped: {exc}"]

    try:
        parsed = extract_json(raw)
        result = TestIdeaRefinementResult.model_validate(parsed)
    except (ValueError, ValidationError) as exc:
        test_design.risks.append(f"LLM refinement ignored: {exc}")
        return ["LLM refinement ignored because output did not match schema."]

    ideas_by_id = {idea.idea_id: idea for idea in test_design.ideas}
    cases_by_idea_id = {
        case.traceability[0]: case
        for case in test_design.test_cases
        if case.traceability
    }
    applied = 0
    for refinement in result.refinements:
        idea = ideas_by_id.get(refinement.idea_id)
        if not idea:
            continue
        case = cases_by_idea_id.get(idea.idea_id)
        if refinement.title:
            idea.title = refinement.title
            if case:
                case.title = refinement.title
        if refinement.reason:
            idea.reason = refinement.reason
        if refinement.expected_description:
            idea.expected.description = refinement.expected_description
            if case:
                case.expected.description = refinement.expected_description
                case.expected_result = [
                    line
                    for line in [
                        f"HTTP status is {case.expected.status}." if case.expected.status is not None else "",
                        case.expected.description,
                    ]
                    if line
                ]
        if refinement.requires_human_review is not None:
            idea.requires_human_review = refinement.requires_human_review
            if case:
                case.requires_human_review = refinement.requires_human_review
        applied += 1
    test_design.risks.extend(result.risks)
    return [f"LLM refinements applied: {applied}."]
