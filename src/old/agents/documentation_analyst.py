from __future__ import annotations

import json
from typing import Any

from old.agents.base import Agent
from old.domain import AgentMessage, ProjectState, ScenarioUnderstanding


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
      "required_data": ["object_id", "user_id"],
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

Business step rules:
- Keep the original scenario order and preserve the main action of each step.
- If one documented step contains several API-triggering actions, preserve every action in the
  business step text. Do not collapse "read/check and then submit/authorize/update" into only
  the first action.
- Do not summarize away lifecycle actions such as create an object, authorize a required external action,
  activate/start an object, complete/close an object, cancel, extend, add related data, validate an identifier,
  or register an event.
- If a step says an employee/operator confirms operational facts before an object becomes active,
  preserve the activation/start meaning, not only the manual record/check.
- Put a system state, status, or displayed result caused by an earlier command into success_criteria,
  not into business_steps as another command. Keep it as a business step only when the document
  explicitly describes a separate actor action or another API-triggering command.
- Business steps may be translated to English, but the operational meaning must stay intact.

Dependency examples:
- "To cancel an object, an active object must already exist" -> requires_state.
- "Run after UC-001 Create object" -> requires_scenario.
- "Requires existing objectId" -> requires_data.
- For requires_scenario, fill required_data with concrete data names needed from that scenario.
- Do not create scenario_dependencies from ordinary request inputs, reference data, actor attributes,
  or availability preconditions unless the document says they must come from another scenario or
  from already existing system state.
- If the scenario uses path placeholders like {{objectId}}, {{sessionId}}, or {{orderId}},
  include those names in required_data for the dependency that provides the existing object.
""".strip(),
            ),
        ]

    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        state.understanding = output
        return state
