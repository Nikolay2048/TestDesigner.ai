from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from data_dependencies import build_generation_binding_tasks
from domain import AgentMessage, GenerationBindingResult, GenerationBindingTask, ProjectState
from generators import GeneratorRegistry


class GenerationBindingAgent(Agent):
    """Chooses static/generated/computed/literal sources for unresolved request fields."""

    name = "Generation Binding"
    output_model = GenerationBindingResult

    def __init__(
        self,
        llm=None,
        generator_registry: GeneratorRegistry | None = None,
        tasks: list[GenerationBindingTask] | None = None,
    ):
        super().__init__(llm)
        self.generator_registry = generator_registry or GeneratorRegistry()
        self.tasks = tasks

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        graph = state.data_dependency_graph
        resolutions = state.dependency_resolutions.resolutions if state.dependency_resolutions else []
        tasks = (
            self.tasks
            if self.tasks is not None
            else build_generation_binding_tasks(graph, resolutions, state, self.generator_registry)
            if graph
            else []
        )
        compact_tasks = [
            {
                "step_id": task.step_id,
                "business_step": task.business_step,
                "operation": task.operation.model_dump(mode="json"),
                "need": task.need.model_dump(mode="json"),
                "static_keys": task.static_keys,
                "available_generators": [generator.name for generator in task.available_generators],
                "available_generator_tools": [
                    self.generator_registry.tool_schema(generator.name)
                    for generator in task.available_generators
                ],
                "business_context": task.business_context,
            }
            for task in tasks
        ]
        return [
            AgentMessage(
                role="system",
                content=(
                    "You choose a data source for request fields that were not resolved from previous responses. "
                    "Use only provided static keys and generator names. Return strict JSON only."
                ),
            ),
            AgentMessage(
                role="user",
                content=f"""
Generation binding tasks:
{json.dumps(compact_tasks, ensure_ascii=False, indent=2)}

Return JSON with this shape:
{{
  "decisions": [
    {{
      "step_id": "s04",
      "target": "$.customer.phone",
      "source": "static|generated|computed|literal|missing|unknown",
      "static_key": null,
      "generator": "phone_number",
      "params": {{"country": "{{{{country_code}}}}", "format": "{{{{phone_format}}}}"}},
      "expression": null,
      "literal": null,
      "confidence": "high|medium|low|none",
      "reason": "why this source is appropriate",
      "requires_human_review": false
    }}
  ],
  "risks": ["global risk"]
}}

Rules:
- Return one decision for every task.
- Use source=static only with a key from the task static_keys.
- Use source=generated only with a name from the task available_generators.
- Generator params must conform to the matching available_generator_tools schema.
- Do not quote integer, number, or boolean generator params.
- Use source=missing when the value must come from a human/test-data/mocked external system.
- Use source=unknown when there is not enough information to choose safely.
- Do not invent generators or static keys.
- Do not provide concrete random values.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        state.generation_bindings = output
        return state
