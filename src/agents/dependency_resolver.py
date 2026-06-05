from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from data_dependencies import build_dependency_resolution_tasks
from domain import AgentMessage, DependencyResolutionTask, DependencyResolverResult, ProjectState


class DependencyResolverAgent(Agent):
    """Chooses previous response fields for request needs from prepared candidates."""

    name = "Dependency Resolver"
    output_model = DependencyResolverResult

    def __init__(self, llm=None, tasks: list[DependencyResolutionTask] | None = None):
        super().__init__(llm)
        self.tasks = tasks

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        graph = state.data_dependency_graph
        tasks = self.tasks if self.tasks is not None else build_dependency_resolution_tasks(graph) if graph else []
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
                    "Choose only candidate_id values from the provided candidates. Return strict JSON only. "
                    "Copy step_id and target exactly from task.need."
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
      "target": "$.path.objectId",
      "selected_candidate_id": "c_s04_id",
      "confidence": "high|medium|low|none",
      "reason": "why this response field is the best source, or why no candidate fits"
    }}
  ],
  "risks": ["global risk"]
}}

Rules:
- Return one resolution for every task.
- target must exactly match task.need.target.
- selected_candidate_id must be one of the task candidates or null.
- Use null and confidence=none when candidates do not describe the required value.
- Do not create new variables, json paths, generators, or static keys.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        tasks = self.tasks
        if tasks is None and state.data_dependency_graph:
            tasks = build_dependency_resolution_tasks(state.data_dependency_graph)
        if tasks:
            output = _normalize_resolutions(output, tasks)
        state.dependency_resolutions = output
        return state


def _normalize_resolutions(
    output: DependencyResolverResult,
    tasks: list[DependencyResolutionTask],
) -> DependencyResolverResult:
    """Keep task identity deterministic even when the LLM rewrites target names."""

    by_step = {resolution.step_id: resolution for resolution in output.resolutions}
    normalized = []
    risks = list(output.risks)

    for task in tasks:
        resolution = by_step.get(task.step_id)
        if not resolution:
            risks.append(f"{task.step_id} {task.need.target}: missing dependency resolution.")
            continue

        candidate_ids = {candidate.candidate_id for candidate in task.candidates}
        selected = resolution.selected_candidate_id
        if selected and selected not in candidate_ids:
            risks.append(
                f"{task.step_id} {task.need.target}: ignored unknown candidate_id {selected}."
            )
            selected = None

        resolution.step_id = task.step_id
        resolution.target = task.need.target
        resolution.selected_candidate_id = selected
        if not selected:
            resolution.confidence = "none"
        normalized.append(resolution)

    return DependencyResolverResult(resolutions=normalized, risks=risks)
