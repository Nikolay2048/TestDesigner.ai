from __future__ import annotations

import json
import re
from typing import Any

from old.agents.base import Agent
from old.domain import AgentMessage, ProjectState, StabilizationDiagnosis, StabilizationFix
from old.generators import GeneratorRegistry


class StabilizationFixerAgent(Agent):
    """Proposes one small patch to the data binding plan."""

    name = "Stabilization Fixer"
    output_model = StabilizationFix

    def __init__(
        self,
        llm=None,
        diagnosis: StabilizationDiagnosis | None = None,
        generator_registry: GeneratorRegistry | None = None,
        fixer_try: int = 1,
        rejected_proposals: list[dict[str, Any]] | None = None,
    ):
        super().__init__(llm)
        self.diagnosis = diagnosis
        self.generator_registry = generator_registry or GeneratorRegistry()
        self.fixer_try = fixer_try
        self.rejected_proposals = rejected_proposals or []

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        fix_context = _build_fix_context(state, self.diagnosis)
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
                    "You propose one small patch to one failed REST step. "
                    "Use only the suspected bindings from the diagnosis, except for a strictly "
                    "validated insert_operation explicitly named by the server and OpenAPI. "
                    "Return strict JSON only. Do not repeat previous failed fixes."
                ),
            ),
            AgentMessage(
                role="user",
                content=f"""
Diagnosis:
{json.dumps(self.diagnosis.model_dump(mode="json") if self.diagnosis else None, ensure_ascii=False, indent=2)}

Fix context:
{json.dumps(fix_context, ensure_ascii=False, indent=2)}

Previous attempts and fixes:
{json.dumps(previous_attempts, ensure_ascii=False, indent=2)}

Current fixer try:
{self.fixer_try}

Rejected proposals from earlier fixer tries for this diagnosis:
{json.dumps(self.rejected_proposals, ensure_ascii=False, indent=2)}

Static test data keys:
{json.dumps(sorted(state.static_test_data.keys()), ensure_ascii=False, indent=2)}

Available generator names:
{json.dumps([item.name for item in self.generator_registry.specs()], ensure_ascii=False, indent=2)}

Available generator tools:
{json.dumps(self.generator_registry.tool_schemas(), ensure_ascii=False, indent=2)}

Return JSON with this shape:
{{
  "attempt": 1,
  "patches": [
    {{
      "patch_type": "use_existing_variable|replace_request_binding|replace_response_extraction|add_response_extraction|replace_generated_params|replace_computed_expression|insert_operation|no_patch",
      "step_id": "step id from diagnosis.suspected_bindings",
      "target": "target from diagnosis.suspected_bindings",
      "variable": null,
      "new_binding": null,
      "new_extraction": null,
      "new_step": null,
      "params": {{"days": 2, "format": "date"}},
      "expression": null,
      "reason": "why this patch should help",
      "why_not_repeating_previous_fix": "why this does not repeat a failed prior patch",
      "requires_human_review": true
    }}
  ],
  "reason": "overall reason",
  "risks": ["risk"]
}}

For insert_operation, new_step must have this exact shape:
{{
  "business_step": "short description of the missing prerequisite action",
  "operation": {{"method": "POST", "path": "/exact/path/from/available_unplanned_operations"}},
  "request_bindings": [
    {{
      "target": "$.requiredField",
      "location": "body|path|query|header",
      "source": "response|static|generated|computed|literal|external_context",
      "variable": "existing_or_new_variable_name",
      "static_key": null,
      "generator": null,
      "params": {{}},
      "json_path": null,
      "expression": null,
      "literal": null,
      "scope": "scenario",
      "source_step_id": "s01",
      "reason": "binding reason"
    }}
  ],
  "response_extractions": [
    {{
      "variable": "created_resource_id",
      "json_path": "$.id",
      "scope": "scenario",
      "reason": "required by the failed later step"
    }}
  ]
}}

Rules:
- Return at most one real patch.
- If no safe patch exists, return one patch with patch_type=no_patch.
- Do not change endpoints, step order, or business steps.
- Use only available generator names and static keys.
- Generator params must conform to the matching available_generator_tools schema.
- Do not quote integer, number, or boolean generator params.
- Patch only fields listed in diagnosis.suspected_bindings.
- insert_operation is the only exception to the binding-target rule. Use it only when
  fix_context.hinted_missing_operations or fix_context.available_unplanned_operations contains
  an exact OpenAPI operation that satisfies the server-described missing prerequisite.
  step_id is the failed step before which new_step must be inserted.
- For insert_operation, copy method/path exactly, bind every required request field, do not invent
  variables, and set requires_human_review=true.
- Never repeat a patch listed in rejected_proposals. Change patch_type or use materially different valid data.
- Read each rejection_reason and address that exact validation failure.
- Prefer use_existing_variable when the current binding source is unknown and a suitable variable already exists in failed_step.resolved_bindings or sibling_bindings_same_step.
- Prefer replace_generated_params when the current binding is generated and only params are wrong.
- Patches based on server behavior should require human review.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        return state


def _build_fix_context(
    state: ProjectState,
    diagnosis: StabilizationDiagnosis | None,
) -> dict[str, Any]:
    if not diagnosis or not state.stabilization or not state.stabilization.attempts:
        return {}

    latest_trace = state.stabilization.attempts[-1].trace
    failed_step_trace = next(
        (step for step in latest_trace.steps if step.step_id == diagnosis.failed_step_id),
        None,
    )
    failed_plan_step = None
    if state.data_binding:
        try:
            failed_index = int(diagnosis.failed_step_id.removeprefix("s")) - 1
        except ValueError:
            failed_index = -1
        if 0 <= failed_index < len(state.data_binding.steps):
            failed_plan_step = state.data_binding.steps[failed_index]

    suspected_targets = {
        item.get("target")
        for item in diagnosis.suspected_bindings
        if item.get("step_id") == diagnosis.failed_step_id and item.get("target")
    }
    suspected_bindings = []
    sibling_bindings = []
    if failed_plan_step:
        for binding in failed_plan_step.request_bindings:
            payload = binding.model_dump(mode="json")
            if binding.target in suspected_targets:
                suspected_bindings.append(payload)
            else:
                sibling_bindings.append(payload)

    return {
        "failed_step": failed_step_trace.model_dump(mode="json") if failed_step_trace else None,
        "suspected_bindings": suspected_bindings,
        "sibling_bindings_same_step": sibling_bindings,
        "allowed_patch_targets": diagnosis.suspected_bindings,
        "available_variables_before_failed_step": _available_variables_before_step(
            state, diagnosis.failed_step_id
        ),
        "hinted_missing_operations": _hinted_missing_operations(state, failed_step_trace),
        "available_unplanned_operations": _available_unplanned_operations(state),
    }


def _available_variables_before_step(state: ProjectState, step_id: str) -> list[dict[str, Any]]:
    if not state.data_binding:
        return []
    try:
        failed_index = int(step_id.removeprefix("s")) - 1
    except ValueError:
        return []
    variables = []
    for index, step in enumerate(state.data_binding.steps[:failed_index], start=1):
        for extraction in step.response_extractions:
            variables.append(
                {
                    "variable": extraction.variable,
                    "json_path": extraction.json_path,
                    "source_step_id": f"s{index:02d}",
                }
            )
    return variables


def _hinted_missing_operations(state: ProjectState, failed_step_trace) -> list[dict[str, Any]]:
    if not failed_step_trace:
        return []
    response_text = json.dumps(failed_step_trace.response_body, ensure_ascii=False)
    referenced = {
        (method.upper(), path.rstrip(".,;:"))
        for method, path in re.findall(
            r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_{}./?-]+)",
            response_text,
            re.IGNORECASE,
        )
    }
    planned = {
        (step.operation.method.upper(), step.operation.path)
        for step in (state.data_binding.steps if state.data_binding else [])
    }
    return [
        _operation_fix_payload(operation)
        for operation in state.operations
        if (operation.method.upper(), operation.path) in referenced
        and (operation.method.upper(), operation.path) not in planned
    ]


def _available_unplanned_operations(state: ProjectState) -> list[dict[str, Any]]:
    planned = {
        (step.operation.method.upper(), step.operation.path)
        for step in (state.data_binding.steps if state.data_binding else [])
    }
    return [
        _operation_fix_payload(operation)
        for operation in state.operations
        if (operation.method.upper(), operation.path) not in planned
    ]


def _operation_fix_payload(operation) -> dict[str, Any]:
    return {
        "method": operation.method,
        "path": operation.path,
        "summary": operation.summary,
        "request_parameters": operation.request_parameters,
        "request_schema": operation.request_schema,
        "response_schemas": operation.response_schemas,
    }
