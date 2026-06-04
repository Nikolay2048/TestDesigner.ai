from __future__ import annotations

import json

from pydantic import ValidationError

from domain import AgentMessage, AgentRun, ProjectState, TestDesignResult, TestIdeaRefinementResult
from llm import LLM, NoLLM, extract_json
from test_design import build_test_design


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
        prompt = _build_refinement_prompt(state)
        notes = _refine_with_llm(state.test_design, self.llm, prompt)
        return state, AgentRun(
            agent_name=self.name,
            status="completed",
            prompt=prompt,
            output=state.test_design.model_dump(mode="json"),
            notes=[
                f"Fields analyzed: {len(state.test_design.basis.fields)}.",
                f"Test ideas generated: {len(state.test_design.ideas)}.",
                f"Test cases assembled: {len(state.test_design.test_cases)}.",
                *notes,
            ],
        )


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
