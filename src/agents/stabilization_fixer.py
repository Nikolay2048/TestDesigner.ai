from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from domain import AgentMessage, ProjectState, StabilizationDiagnosis, StabilizationFix
from generators import GeneratorRegistry


class StabilizationFixerAgent(Agent):
    """Proposes one small patch to the data binding plan."""

    name = "Stabilization Fixer"
    output_model = StabilizationFix

    def __init__(
        self,
        llm=None,
        diagnosis: StabilizationDiagnosis | None = None,
        generator_registry: GeneratorRegistry | None = None,
    ):
        super().__init__(llm)
        self.diagnosis = diagnosis
        self.generator_registry = generator_registry or GeneratorRegistry()

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        previous_attempts = [
            {
                "attempt": attempt.attempt,
                "trace_status": attempt.trace.status,
                "failed_step_id": attempt.trace.failed_step_id,
                "failure": attempt.trace.failure,
                "diagnosis": attempt.diagnosis.model_dump(mode="json") if attempt.diagnosis else None,
                "fix": attempt.fix.model_dump(mode="json") if attempt.fix else None,
                "applied_patches": [patch.model_dump(mode="json") for patch in attempt.applied_patches],
            }
            for attempt in (state.stabilization.attempts if state.stabilization else [])
        ]
        return [
            AgentMessage(
                role="system",
                content=(
                    "You propose one small patch to a DataBindingPlan to make the next executor retry pass. "
                    "Return strict JSON only. Do not repeat previous failed fixes."
                ),
            ),
            AgentMessage(
                role="user",
                content=f"""
Diagnosis:
{json.dumps(self.diagnosis.model_dump(mode="json") if self.diagnosis else None, ensure_ascii=False, indent=2)}

Current data binding plan:
{json.dumps(state.data_binding.model_dump(mode="json") if state.data_binding else None, ensure_ascii=False, indent=2)}

Previous attempts and fixes:
{json.dumps(previous_attempts, ensure_ascii=False, indent=2)}

Static test data keys:
{json.dumps(sorted(state.static_test_data.keys()), ensure_ascii=False, indent=2)}

Available generators:
{json.dumps([item.model_dump(mode="json") for item in self.generator_registry.specs()], ensure_ascii=False, indent=2)}

Return JSON with this shape:
{{
  "attempt": 1,
  "patches": [
    {{
      "patch_type": "replace_request_binding|replace_response_extraction|add_response_extraction|replace_generated_params|replace_computed_expression|no_patch",
      "step_id": "s04",
      "target": "$.customer.driverLicenseNo",
      "variable": null,
      "new_binding": {{
        "target": "$.customer.driverLicenseNo",
        "location": "body",
        "source": "generated",
        "variable": "customer_driverLicenseNo",
        "generator": "uuid",
        "params": {{}},
        "scope": "step",
        "policy": "stabilization_uuid_driver_license",
        "reason": "Server requires driverLicenseNo."
      }},
      "new_extraction": null,
      "params": {{}},
      "expression": null,
      "reason": "why this patch should help",
      "why_not_repeating_previous_fix": "why this does not repeat a failed prior patch",
      "requires_human_review": true
    }}
  ],
  "reason": "overall reason",
  "risks": ["risk"]
}}

Rules:
- Return at most one real patch.
- If no safe patch exists, return one patch with patch_type=no_patch.
- Do not change endpoints, step order, or business steps.
- Use only available generator names and static keys.
- Patches based on server behavior should require human review.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        return state
