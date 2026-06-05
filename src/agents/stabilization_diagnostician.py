from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from domain import AgentMessage, ProjectState, StabilizationDiagnosis


class StabilizationDiagnosticianAgent(Agent):
    """Explains why executor failed without changing the plan."""

    name = "Stabilization Diagnostician"
    output_model = StabilizationDiagnosis

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        latest_attempt = state.stabilization.attempts[-1] if state.stabilization and state.stabilization.attempts else None
        return [
            AgentMessage(
                role="system",
                content=(
                    "You diagnose why a REST happy path execution failed. "
                    "Do not propose a full plan. Do not change data. Return strict JSON only."
                ),
            ),
            AgentMessage(
                role="user",
                content=f"""
Current data binding plan:
{json.dumps(state.data_binding.model_dump(mode="json") if state.data_binding else None, ensure_ascii=False, indent=2)}

Latest executor attempt:
{json.dumps(latest_attempt.trace.model_dump(mode="json") if latest_attempt else None, ensure_ascii=False, indent=2)}

Previous attempts:
{json.dumps([
    {
        "attempt": item.attempt,
        "status": item.trace.status,
        "failed_step_id": item.trace.failed_step_id,
        "failure": item.trace.failure,
        "diagnosis": item.diagnosis.model_dump(mode="json") if item.diagnosis else None,
        "applied_patches": [patch.model_dump(mode="json") for patch in item.applied_patches],
    }
    for item in (state.stabilization.attempts[:-1] if state.stabilization else [])
], ensure_ascii=False, indent=2)}

Return JSON with this shape:
{{
  "attempt": 1,
  "failed_step_id": "s04",
  "failure_type": "missing_request_data|invalid_request_data|bad_response_extraction|http_error|unknown",
  "summary": "short diagnosis",
  "evidence": ["evidence from request, response, trace, or plan"],
  "suspected_bindings": [
    {{
      "step_id": "s04",
      "target": "$.payload.requiredField",
      "problem": "source is unknown"
    }}
  ],
  "recommended_fix_type": "replace_request_binding",
  "confidence": "high|medium|low|none",
  "requires_human_review": true
}}

Rules:
- Explain the failure using trace evidence.
- Do not invent server responses.
- Do not propose more than the fix type.
- If the same fix failed before, mention it in evidence.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        return state
