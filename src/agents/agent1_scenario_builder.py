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
import re
from typing import Any, Dict

from langchain_core.messages import HumanMessage, SystemMessage

from src.models.scenario import ScenarioStabilizationInput, VarSource
from src.modules.swagger_parser import EndpointDescriptor, ParsedSpec
from src.utils.config import AppConfig
from src.utils.llm_factory import create_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path correction helper
# ---------------------------------------------------------------------------


def _normalize_path_against_spec(generated_path: str, spec_paths: list[str]) -> str | None:
    """
    Try to map a hallucinated path back to the correct spec path.

    Strategy (in order):
    1. Direct match (already correct) — return as-is.
    2. Replace the leading path prefix with each prefix found in the spec.
       E.g. ``/api/accidents/{id}`` → try ``/v1/accidents/{id}`` (using spec prefix ``/v1``).
    3. Strip leading segments one by one and re-try with spec prefixes.

    Returns the matched spec path template (e.g. ``/v1/accidents/{accidentId}``)
    or ``None`` if no correction found.
    """
    def _spec_to_regex(spec_path: str) -> re.Pattern:
        """``/v1/accidents/{accidentId}`` → regex matching any concrete values."""
        # Replace {param} BEFORE re.escape so braces don't get escaped
        pattern = re.sub(r"\{[^}]+\}", "___PARAM___", spec_path)
        pattern = re.escape(pattern)
        pattern = pattern.replace("___PARAM___", "[^/]+")
        return re.compile(r"^" + pattern + r"$")

    regexes = [(_spec_to_regex(p), p) for p in spec_paths]

    def _try(path: str) -> str | None:
        for rx, spec_path in regexes:
            if rx.match(path):
                return spec_path
        return None

    # 1. Direct match
    result = _try(generated_path)
    if result:
        return result

    # 2. Detect spec prefixes (unique first segments, e.g. "/v1", "/api/v1")
    #    and try replacing the generated path's first segment(s) with them.
    spec_prefixes: set[str] = set()
    for sp in spec_paths:
        segs = sp.split("/")  # ['', 'v1', 'accidents', ...]
        if len(segs) >= 2 and segs[1]:
            spec_prefixes.add("/" + segs[1])          # /v1
        if len(segs) >= 3 and segs[2]:
            spec_prefixes.add("/" + segs[1] + "/" + segs[2])  # /v1/accidents (deeper prefix)

    gen_segs = generated_path.split("/")  # ['', 'api', 'accidents', ...]

    # Try replacing 1, 2, 3 leading segments of generated_path with each spec prefix
    for skip in range(1, min(4, len(gen_segs))):
        tail = "/" + "/".join(gen_segs[skip + 1:]) if gen_segs[skip + 1:] else ""
        for prefix in sorted(spec_prefixes, key=len, reverse=True):
            candidate = prefix + tail
            result = _try(candidate)
            if result:
                return result

    return None

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

- The "path" field must be the EXACT path template from the API SPEC section below.
- ONLY use paths that literally appear in the API SPEC. NEVER invent paths.
- Paths start exactly as shown in the spec (e.g. "/v1/accidents/{accidentId}").
  NEVER change the prefix — do NOT use "/api/", "/accidents/", or any other prefix.
- NEVER embed {{variables}} or query strings directly into the path string.
- NEVER write "/v1/bookings/{{bookingId}}" — keep the original {paramName} placeholder.
- NEVER write "/v1/vehicles/available?city={{city}}" — put query params in query_params dict.
- For every {paramName} in the path template, add a matching entry to path_params:
    path_params: {"bookingId": "{{bookingId}}", "accidentId": "{{accidentId}}"}

## query_params rules

- ALWAYS use the query_params dict for URL query parameters.
- NEVER append ?key=value to the path string.
- Example: query_params: {"city": "{{city}}"}

## extract_vars rules

For each step, list every variable that SUBSEQUENT steps will need:
- Key = variable name (no braces), e.g. "bookingId"
- Value = JSONPath into the response body — MUST start with "$."
- Examples: "$.bookingId", "$.accidentId", "$.participantId", "$.claimId"
- For arrays use index notation: "$.items[0].vehicleId"
- NEVER use "response.fieldName" — always use "$.fieldName"
- The field name must match exactly what appears in the API response example in the spec.

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
        spec_paths = [ep.path for ep in spec.endpoints]
        result = self._normalize(result, spec_paths=spec_paths)
        result = self._classify_vars(result, constants)

        logger.info(
            "Agent1: done — scenario='%s'  steps=%d  vars=%d (const=%d ctx=%d gen=%d)",
            result.scenario_name,
            len(result.steps),
            len(result.var_sources),
            sum(1 for v in result.var_sources if v.kind == "constant"),
            sum(1 for v in result.var_sources if v.kind == "context"),
            sum(1 for v in result.var_sources if v.kind == "generate"),
        )
        return result

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(
        result: ScenarioStabilizationInput,
        spec_paths: list[str] | None = None,
    ) -> ScenarioStabilizationInput:
        """
        Fix common LLM structured-output quirks.

        Fixes applied (in order):
        1. Strip query string accidentally embedded in ``path``.
        2. Convert ``{{param}}`` → ``{param}`` in path templates.
        3. Fix wrong path prefix (``/api/``, ``/accidents/`` …) using spec path list.
        4. Fix ``extract_vars`` values: ``response.field`` → ``$.field``,
           ``field`` → ``$.field`` (ensure JSONPath ``$.`` prefix).
        5. Unwrap ``expected: {"value": X}`` in assertions.
        """
        import re as _re

        for step in result.steps:

            # ── 1. Strip embedded query string ──────────────────────────────
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

            # ── 2. {{param}} → {param} in path ──────────────────────────────
            fixed_path = _re.sub(r"\{\{(\w+)\}\}", r"{\1}", step.path)
            if fixed_path != step.path:
                for var in _re.findall(r"\{(\w+)\}", fixed_path):
                    step.path_params.setdefault(var, f"{{{{{var}}}}}")
                step.path = fixed_path
                logger.debug(
                    "Agent1.normalize: fixed path template in step %d → %s",
                    step.step_num, step.path,
                )

            # ── 3. Fix wrong path prefix using spec path list ────────────────
            if spec_paths:
                # Normalise the generated path to a template for matching
                # (replace concrete values like "accident-1" with placeholder)
                path_template = _re.sub(r"/[a-zA-Z0-9_-]+-\d+", "/{x}", step.path)
                # Build a strip-and-try strategy: remove leading segments and see
                # if the remainder matches a known spec path template
                matched = _normalize_path_against_spec(step.path, spec_paths)
                if matched and matched != step.path:
                    logger.warning(
                        "Agent1.normalize: corrected wrong path in step %d: %r → %r",
                        step.step_num, step.path, matched,
                    )
                    step.path = matched
                    # Re-populate path_params for the corrected path
                    for var in _re.findall(r"\{(\w+)\}", matched):
                        step.path_params.setdefault(var, f"{{{{{var}}}}}")

            # ── 4. Fix extract_vars JSONPath ─────────────────────────────────
            if step.extract_vars:
                fixed_ev: dict[str, str] = {}
                for var_name, jsonpath in step.extract_vars.items():
                    original = jsonpath
                    # "response.field" → "$.field"
                    if _re.match(r"^response\.", jsonpath):
                        jsonpath = "$." + jsonpath[len("response."):]
                    # "field" (no prefix) → "$.field"
                    elif not jsonpath.startswith("$"):
                        jsonpath = "$." + jsonpath.lstrip(".")
                    # "$[" is also valid (array root)
                    if jsonpath != original:
                        logger.debug(
                            "Agent1.normalize: fixed extract_vars[%r] %r → %r in step %d",
                            var_name, original, jsonpath, step.step_num,
                        )
                    fixed_ev[var_name] = jsonpath
                step.extract_vars = fixed_ev

            # ── 5. Fix assertion JSONPath prefix ────────────────────────────
            for assertion in step.assertions:
                if assertion.path and not assertion.path.startswith("$"):
                    if _re.match(r"^response\.", assertion.path):
                        assertion.path = "$." + assertion.path[len("response."):]
                    else:
                        assertion.path = "$." + assertion.path.lstrip(".")

            # ── 6. Unwrap {"value": X} in assertion.expected ────────────────
            for assertion in step.assertions:
                if isinstance(assertion.expected, dict):
                    raw = assertion.expected
                    if "value" in raw and len(raw) == 1:
                        assertion.expected = raw["value"]
                        logger.debug(
                            "Agent1.normalize: unwrapped assertion expected "
                            "{'value': X} → %r in step %d",
                            assertion.expected, step.step_num,
                        )
                    else:
                        logger.warning(
                            "Agent1.normalize: dropping unsupported expected=%r "
                            "in step %d assertion '%s'; changing to not_null",
                            raw, step.step_num, assertion.description,
                        )
                        assertion.expected = None
                        assertion.operator = "not_null"

        return result

    # ------------------------------------------------------------------
    # Variable source classification  (deterministic, no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_vars(
        result: ScenarioStabilizationInput,
        constants: Dict[str, Any],
    ) -> ScenarioStabilizationInput:
        """
        Classify every ``{{varName}}`` reference in the scenario into one of:

        * ``constant``  — name is present in ``constants`` dict
        * ``context``   — name appears in ``extract_vars`` of a prior step
        * ``generate``  — must be generated at runtime (Agent 3 / Postman pre-request)

        The classification is purely algorithmic: no LLM involved.
        The result is stored in ``result.var_sources``.
        """
        import re
        _VAR_RE = re.compile(r"\{\{(\w+)\}\}")

        constant_keys: set[str] = set(constants.keys())

        # Build extraction map: var_name → step_num of the step that produces it
        extracted_by: Dict[str, int] = {}
        for step in result.steps:
            for var_name in step.extract_vars:
                extracted_by[var_name] = step.step_num

        # Collect ALL {{var}} references across every step field
        def _refs_from_obj(obj: Any) -> set[str]:
            found: set[str] = set()
            if isinstance(obj, str):
                found.update(_VAR_RE.findall(obj))
            elif isinstance(obj, dict):
                for v in obj.values():
                    found |= _refs_from_obj(v)
            elif isinstance(obj, list):
                for item in obj:
                    found |= _refs_from_obj(item)
            return found

        all_refs: set[str] = set()
        for step in result.steps:
            all_refs |= _refs_from_obj(step.path)
            all_refs |= _refs_from_obj(step.path_params)
            all_refs |= _refs_from_obj(step.query_params)
            all_refs |= _refs_from_obj(step.body)
            # Also include var names that appear in path_params values
            for v in (step.path_params or {}).values():
                all_refs |= _refs_from_obj(v)

        sources: list[VarSource] = []
        for var_name in sorted(all_refs):
            if var_name in constant_keys:
                sources.append(VarSource(name=var_name, kind="constant"))
            elif var_name in extracted_by:
                sources.append(
                    VarSource(
                        name=var_name,
                        kind="context",
                        provided_by_step=extracted_by[var_name],
                    )
                )
            else:
                sources.append(VarSource(name=var_name, kind="generate"))
                logger.debug(
                    "Agent1.classify_vars: '%s' → generate (not in constants or extract_vars)",
                    var_name,
                )

        result.var_sources = sources

        # Log summary
        by_kind = {"constant": [], "context": [], "generate": []}
        for vs in sources:
            by_kind[vs.kind].append(vs.name)
        if by_kind["generate"]:
            logger.info(
                "Agent1.classify_vars: vars to generate at runtime: %s",
                by_kind["generate"],
            )
        if by_kind["context"]:
            logger.debug(
                "Agent1.classify_vars: context vars (from step responses): %s",
                by_kind["context"],
            )

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
