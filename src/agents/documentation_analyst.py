from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from domain import AgentMessage, ProjectState, ScenarioUnderstanding


class DocumentationAnalystAgent(Agent):
    name = "Documentation Analyst"
    output_model = ScenarioUnderstanding

    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        return [
            AgentMessage(
                role="system",
                content=(
                    "You are a senior QA analyst. Read a system-analysis scenario and extract "
                    "only information useful for REST API test design. Endpoint-like strings are extracted "
                    "by deterministic code and provided to you. Do not invent endpoints. If the scenario "
                    "depends on another scenario, existing system state, or pre-created data, preserve that "
                    "as scenario_dependencies. Return strict JSON only."
                ),
            ),
            AgentMessage(
                role="user",
                content=f"""
Scenario title:
{state.scenario.title}

Scenario text:
{state.scenario.text}

Endpoint mentions found by deterministic code:
{json.dumps([item.model_dump(mode="json") for item in state.scenario.raw_endpoint_mentions], ensure_ascii=False, indent=2)}

Return JSON with this shape:
{{
  "title": "short scenario title",
  "goal": "business goal",
  "actors": ["actor names"],
  "preconditions": ["precondition"],
  "business_steps": ["ordered business step"],
  "business_rules": ["rule or constraint"],
  "success_criteria": ["condition for successful scenario completion"],
  "negative_conditions": ["explicit failure/error condition from the document"],
  "endpoint_mentions": [
    {{
      "method": "GET or POST or null if method is absent",
      "path": "/api/path/from/document",
      "location": "header|step|rule|unknown",
      "related_step": "business step text if endpoint belongs to a step, otherwise null",
      "note": "why this endpoint was mentioned"
    }}
  ],
  "scenario_dependencies": [
    {{
      "kind": "requires_scenario|requires_state|requires_data",
      "reference": "UC name/id or scenario name if written, otherwise null",
      "required_state": "state that must already exist, otherwise null",
      "required_data": ["reservation_id", "user_id"],
      "reason": "why current scenario needs this dependency"
    }}
  ],
  "open_questions": ["missing or ambiguous detail"]
}}

Endpoint rules:
- endpoint_mentions must be based only on "Endpoint mentions found by deterministic code".
- Do not add, remove, rewrite, normalize, or infer endpoints.
- If the deterministic list is empty, return "endpoint_mentions": [].
- You may only classify each provided endpoint by location, related_step, and note.

Dependency examples:
- "To cancel a booking, an active booking must already exist" -> requires_state.
- "Run after UC-001 Create reservation" -> requires_scenario.
- "Requires existing reservationId" -> requires_data.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        state.understanding = output
        return state
