"""
Agent 1 — Scenario Builder.

Reads a business scenario (plain text / markdown) and a parsed OpenAPI spec,
then uses an LLM to produce a :class:`ScenarioStabilizationInput` — a fully
structured list of :class:`TestStep` objects that Agent 2 can execute.

How it works
------------
1. The agent formats the API spec into a concise, LLM-friendly text block.
2. It constructs a detailed system prompt and a user message containing the
   scenario text, the API summary, and the runtime constants.
3. The LLM is called with ``with_structured_output(ScenarioStabilizationInput)``
   so the response is validated and parsed automatically.

Variable references
-------------------
The LLM is instructed to use ``{{variableName}}`` placeholders for:
* Constants loaded from ``constants.json`` (e.g. ``{{userId}}``, ``{{city}}``)
* Values extracted from previous step responses (e.g. ``{{bookingId}}``)
* Dynamic values Agent 3 will generate at runtime (e.g. ``{{startDate}}``)

Usage::

    from src.agents.agent1_scenario_builder import ScenarioBuilderAgent
    from src.utils.config import get_config
    from src.modules.swagger_parser import SwaggerParser

    cfg = get_config()
    spec = SwaggerParser("data/openapi.yaml").parse()
    scenario_text = open("data/scenario.md", encoding="utf-8").read()
    constants = {"userId": "user-1", "city": "Moscow"}

    agent = ScenarioBuilderAgent(cfg)
    result = agent.build(scenario_text, spec, constants)
    print(result.model_dump_json(indent=2))
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from langchain_core.messages import HumanMessage, SystemMessage

from src.models.scenario import ScenarioStabilizationInput
from src.modules.swagger_parser import EndpointDescriptor, ParsedSpec
from src.utils.config import AppConfig
from src.utils.llm_factory import create_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior QA automation engineer. Your task is to analyze a business \
test scenario written in Russian and a REST API specification, then produce a \
structured, machine-executable test plan.

## Your output

Return a ScenarioStabilizationInput object with an ordered list of TestStep \
objects — one step per API request.

## Variable reference rules

Use {{variableName}} placeholders (double curly braces) wherever a value is \
dynamic or comes from another source:

| Source | Example placeholder |
|--------|-------------------|
| Constant provided in CONSTANTS section | {{user_id}}, {{city}} |
| Value extracted from a previous step's response | {{bookingId}}, {{vehicleId}} |
| Value that must be generated at runtime | {{startDate}} (future date-time) |

Use the EXACT key names from the CONSTANTS section (e.g. {{user_id}} not {{userId}}).
NEVER hard-code IDs or dates that are runtime values — always use the {{name}} form.

## Path rules — CRITICAL

- The "path" field must be the EXACT path template from the spec, e.g. "/v1/bookings/{bookingId}".
- NEVER embed {{variables}} or query strings directly into the path string.
- NEVER write "/v1/bookings/{{bookingId}}" — keep the original {bookingId} placeholder.
- NEVER write "/v1/vehicles/available?city={{city}}" — put query params in query_params dict.
- For every {paramName} in the path template, add a matching entry to path_params:
    path_params: {"bookingId": "{{bookingId}}"}

## query_params rules

- ALWAYS use the query_params dict for URL query parameters.
- NEVER append ?key=value to the path string.
- Example: query_params: {"city": "{{city}}"}

## extract_vars rules

For each step, list every variable that SUBSEQUENT steps will need:
- Key = variable name (no braces), e.g. "bookingId"
- Value = JSONPath into the response body, e.g. "$.bookingId"
- For arrays use index notation: "$.items[0].vehicleId"

## Assertion rules

Write assertions that verify the business outcomes stated in the scenario:
- Use operator "eq" to check exact field values (enum strings like "CREATED", "CANCELLED").
- Use operator "not_null" to confirm IDs or objects were returned.
- Use operator "exists" to confirm a key is present when value is unknown.
- The "path" must be a valid JSONPath starting with "$.".
- CRITICAL: the "expected" field must be a plain scalar value — string, number, \
  or boolean. NEVER wrap it in an object. Correct: expected="CREATED". \
  Wrong: expected={"value": "CREATED"}.
- NEVER assert an exact count on list sizes (e.g. $.count == 1). \
  The real server may return a different number than the spec example. \
  Instead, check that the first item exists: path="$.items[0].vehicleId", \
  operator="not_null".

## Step mapping rules

- Map EVERY API call in the scenario — do not skip any.
- Do NOT add extra steps beyond what the scenario describes.
- expected_status_code: if the scenario explicitly states an HTTP error code \
  (400, 403, 404, 409, etc.), use that as expected_status_code. \
  Only use 200/201 for steps that are expected to succeed.
- For the request body, include ONLY fields that appear in the API spec schema.

## Error scenario rules

When a step is expected to FAIL (non-2xx status code):
- Set expected_status_code to the stated error code (400, 403, 404, 409, …).
- Add an assertion on the error code field: path="$.code", operator="not_null".
- If the scenario states a specific error code string (e.g. VEHICLE_NOT_AVAILABLE), \
  add: path="$.code", operator="eq", expected="VEHICLE_NOT_AVAILABLE".
- Do NOT add extract_vars for error steps — there is nothing useful to extract.

## Literal values in negative tests

When the scenario explicitly states a fixed literal value for testing \
(e.g. startDate="2020-01-01T00:00:00Z", vehicleId="vehicle-nonexistent", \
userId="user-blocked"), use that exact literal string in the body or path_params \
— do NOT replace it with a {{variable}} placeholder.

## Response schema awareness

When assertions reference response fields, use the field names exactly as they \
appear in the API response examples provided in the API SPEC section.
"""

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ScenarioBuilderAgent:
    """
    Agent 1: Scenario Builder.

    Converts a business scenario + ParsedSpec → ScenarioStabilizationInput.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._llm = create_llm(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        scenario_text: str,
        spec: ParsedSpec,
        constants: Dict[str, Any],
    ) -> ScenarioStabilizationInput:
        """
        Run Agent 1.

        Args:
            scenario_text: Raw text of the business scenario (e.g. scenario.md).
            spec:          Parsed and fully-resolved OpenAPI spec.
            constants:     Key-value pairs from constants.json.

        Returns:
            A validated :class:`ScenarioStabilizationInput` ready for Agent 2.
        """
        logger.info(
            "Agent1: building scenario plan  provider=%s  endpoints=%d",
            self.config.llm.provider,
            len(spec.endpoints),
        )

        structured_llm = self._llm.with_structured_output(ScenarioStabilizationInput)

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=self._build_human_message(scenario_text, spec, constants)),
        ]

        logger.debug("Agent1: invoking LLM...")
        result: ScenarioStabilizationInput = structured_llm.invoke(messages)
        result = self._normalize(result)

        logger.info(
            "Agent1: done — scenario='%s'  steps=%d",
            result.scenario_name,
            len(result.steps),
        )
        return result

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(result: ScenarioStabilizationInput) -> ScenarioStabilizationInput:
        """
        Fix common LLM structured-output quirks:

        * ``expected: {"value": "X"}`` → ``expected: "X"``
        * ``path`` contains embedded query string → strip and move to query_params
        * ``path`` contains ``{{var}}`` instead of ``{var}`` → restore template form
        """
        for step in result.steps:
            # --- Fix path: strip query string accidentally embedded in path ---
            if "?" in step.path:
                path_part, qs = step.path.split("?", 1)
                step.path = path_part
                if step.query_params is None:
                    step.query_params = {}
                for pair in qs.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        step.query_params.setdefault(k, v)
                logger.debug(
                    "Agent1.normalize: stripped query string from path in step %d",
                    step.step_num,
                )

            # --- Fix path: {{param}} → {param} (restore template placeholders) ---
            import re
            fixed_path = re.sub(r"\{\{(\w+)\}\}", r"{\1}", step.path)
            if fixed_path != step.path:
                # Rebuild path_params from the embedded var references
                for var in re.findall(r"\{(\w+)\}", fixed_path):
                    step.path_params.setdefault(var, f"{{{{{var}}}}}")
                step.path = fixed_path
                logger.debug(
                    "Agent1.normalize: fixed path template in step %d → %s",
                    step.step_num, step.path,
                )

            # --- Fix assertions: unwrap {"value": X} and similar dict wrappers ---
            for assertion in step.assertions:
                if isinstance(assertion.expected, dict):
                    raw = assertion.expected
                    # {"value": "CREATED"} → "CREATED"
                    if "value" in raw and len(raw) == 1:
                        assertion.expected = raw["value"]
                        logger.debug(
                            "Agent1.normalize: unwrapped assertion expected "
                            "{'value': X} → %r in step %d",
                            assertion.expected, step.step_num,
                        )
                    else:
                        # Unknown dict structure — discard and use not_null
                        logger.warning(
                            "Agent1.normalize: dropping unsupported expected=%r "
                            "in step %d assertion '%s'; changing to not_null",
                            raw, step.step_num, assertion.description,
                        )
                        assertion.expected = None
                        assertion.operator = "not_null"

        return result

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_human_message(
        self,
        scenario_text: str,
        spec: ParsedSpec,
        constants: Dict[str, Any],
    ) -> str:
        parts: list[str] = []

        # --- CONSTANTS ---
        parts.append("# CONSTANTS")
        parts.append(
            "These key-value pairs are available as {{name}} placeholders. "
            "Use them in query_params, body, and path_params where appropriate."
        )
        for key, value in constants.items():
            parts.append(f"  {{{{ {key} }}}} = {json.dumps(value, ensure_ascii=False)}")
        parts.append("")

        # --- API SPEC ---
        parts.append("# API SPEC")
        parts.append(f"Base URL: {spec.base_url}")
        parts.append("")
        for ep in spec.endpoints:
            parts.append(self._format_endpoint(ep))

        # --- SCENARIO ---
        parts.append("# SCENARIO")
        parts.append(scenario_text.strip())
        parts.append("")

        # --- TASK ---
        parts.append("# TASK")
        parts.append(
            "Analyze the SCENARIO and produce a ScenarioStabilizationInput. "
            "Map every API call described in the scenario to a TestStep. "
            "Use {{variableName}} placeholders as described in the system prompt."
        )

        return "\n".join(parts)

    @staticmethod
    def _format_endpoint(ep: EndpointDescriptor) -> str:
        """Return a compact, LLM-friendly description of one endpoint."""
        lines: list[str] = []
        lines.append(f"## {ep.method} {ep.path}")

        if ep.summary:
            lines.append(f"Summary: {ep.summary}")
        if ep.description:
            lines.append(f"Description: {ep.description}")

        if ep.parameters:
            lines.append("Parameters:")
            for p in ep.parameters:
                req_mark = " (required)" if p.required else ""
                desc = f" — {p.description}" if p.description else ""
                example = f"  example={json.dumps(p.example)}" if p.example is not None else ""
                lines.append(f"  [{p.location}] {p.name}{req_mark}{desc}{example}")

        if ep.request_body:
            rb = ep.request_body
            lines.append(f"Request body (required={rb.required}, content-type={rb.content_type}):")
            if rb.example:
                lines.append(
                    "  Example: " + json.dumps(rb.example, indent=4, ensure_ascii=False)
                )
            elif rb.schema_:
                # Show only top-level property names as a hint
                props = rb.schema_.get("properties", {})
                if props:
                    lines.append(f"  Fields: {', '.join(props.keys())}")

        if ep.responses:
            lines.append("Responses:")
            for resp in ep.responses:
                lines.append(f"  {resp.status_code}: {resp.description}")
                if resp.example is not None:
                    lines.append(
                        "    Example: "
                        + json.dumps(resp.example, indent=6, ensure_ascii=False)
                    )

        lines.append("")
        return "\n".join(lines)
