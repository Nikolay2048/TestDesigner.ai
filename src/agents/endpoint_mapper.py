from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from domain import AgentMessage, EndpointMappingResult, ProjectState


class EndpointMapperAgent(Agent):
    """Maps business steps to OpenAPI operations."""

    name = "Endpoint Mapper"
    output_model = EndpointMappingResult

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        understanding = state.understanding
        operations = [
            {
                "method": operation.method,
                "path": operation.path,
                "summary": operation.summary,
                "response_statuses": operation.response_statuses,
            }
            for operation in state.operations
        ]
        return [
            AgentMessage(
                role="system",
                content=(
                    "You map business scenario steps to available REST API operations. "
                    "Use only operations from the provided list. Return strict JSON only."
                ),
            ),
            AgentMessage(
                role="user",
                content=f"""
Business steps:
{json.dumps(understanding.business_steps if understanding else [], ensure_ascii=False, indent=2)}

Endpoint mentions from documentation:
{json.dumps([item.model_dump(mode="json") for item in understanding.endpoint_mentions] if understanding else [], ensure_ascii=False, indent=2)}

Available OpenAPI operations:
{json.dumps(operations, ensure_ascii=False, indent=2)}

Return JSON with this shape:
{{
  "mappings": [
    {{
      "business_step": "exact business step text",
      "operations": [
        {{"method": "GET", "path": "/locations"}}
      ],
      "source": "explicit_mention|semantic_match|none",
      "confidence": "high|medium|low",
      "reason": "why these operations match the step",
      "risks": ["risk or uncertainty"]
    }}
  ],
  "unmapped_steps": ["business step that has no matching operation"],
  "risks": ["global mapping risk"]
}}

Rules:
- Use only exact method/path pairs from Available OpenAPI operations.
- Do not invent endpoints.
- If an endpoint mention exists and exactly matches OpenAPI, prefer it and use source=explicit_mention.
- If an endpoint mention is absent from OpenAPI, do not use it; add a risk.
- One business step may map to zero, one, or multiple operations.
- Every business step must appear either in mappings or unmapped_steps.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        state.endpoint_mapping = output
        return state

