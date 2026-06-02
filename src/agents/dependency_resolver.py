from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from data_dependencies import build_dependency_resolution_tasks
from domain import AgentMessage, DependencyResolverResult, ProjectState


class DependencyResolverAgent(Agent):
    """Chooses previous response fields for request needs from prepared candidates."""

    name = "Dependency Resolver"
    output_model = DependencyResolverResult

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        graph = state.data_dependency_graph
        tasks = build_dependency_resolution_tasks(graph) if graph else []
        compact_tasks = [
            {
                "step_id": task.step_id,
                "business_step": task.business_step,
                "operation": task.operation.model_dump(mode="json"),
                "need": task.need.model_dump(mode="json"),
                "candidates": [candidate.model_dump(mode="json") for candidate in task.candidates],
            }
            for task in tasks
        ]
        return [
            AgentMessage(
                role="system",
                content=(
                    "You choose whether a request field should be filled from a previous API response. "
                    "Choose only candidate_id values from the provided candidates. Return strict JSON only."
                ),
            ),
            AgentMessage(
                role="user",
                content=f"""
Dependency resolution tasks:
{json.dumps(compact_tasks, ensure_ascii=False, indent=2)}

Return JSON with this shape:
{{
  "resolutions": [
    {{
      "step_id": "s05",
      "target": "$.reservationId",
      "selected_candidate_id": "c_s04_id",
      "confidence": "high|medium|low|none",
      "reason": "why this response field is the best source, or why no candidate fits"
    }}
  ],
  "risks": ["global risk"]
}}

Rules:
- Return one resolution for every task.
- selected_candidate_id must be one of the task candidates or null.
- Use null and confidence=none when candidates do not describe the required value.
- Do not create new variables, json paths, generators, or static keys.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        state.dependency_resolutions = output
        return state
