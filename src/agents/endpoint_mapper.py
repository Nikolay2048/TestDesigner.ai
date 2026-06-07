from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from domain import AgentMessage, EndpointMappingResult, ProjectState


class EndpointMapperAgent(Agent):
    """Maps business steps to OpenAPI operations."""

    name = "Endpoint Mapper"
    output_model = EndpointMappingResult

    def __init__(self, llm=None, business_steps: list[str] | None = None):
        super().__init__(llm)
        self.business_steps = business_steps

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        understanding = state.understanding
        business_steps = (
            self.business_steps
            if self.business_steps is not None
            else understanding.business_steps if understanding else []
        )
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
{json.dumps(business_steps, ensure_ascii=False, indent=2)}

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
  "unmapped_steps": [
    {{
      "business_step": "business step that has no matching operation",
      "reason": "why no separate API operation is needed or why no OpenAPI operation matches"
    }}
  ],
  "risks": ["global mapping risk"]
}}

Rules:
- Use only exact method/path pairs from Available OpenAPI operations.
- Do not invent endpoints.
- If an endpoint mention exists and exactly matches OpenAPI, prefer it and use source=explicit_mention.
- If an endpoint mention is absent from OpenAPI, do not use it; add a risk.
- One business step may map to zero, one, or multiple operations.
- Every business step must appear either in mappings or unmapped_steps.
- Do not put a step into unmapped_steps just because matching is hard.
- Use unmapped_steps only when a separate API call is not needed, the behavior is an expected outcome of a previous API call, the action is manual/non-API, or OpenAPI has no matching operation.
- Preserve lifecycle API steps that create IDs needed later. If a later operation needs a path parameter like objectId, orderId, sessionId, or itemId, include the earlier operation that creates, locates, or activates that resource.
- Do not skip API-like business actions such as external authorization, start/activate, complete/close, cancel, extend, add related data, validate an identifier, or register an event when OpenAPI has a matching operation.
- For every unmapped step, write a concrete reason.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        state.endpoint_mapping = output
        return state
