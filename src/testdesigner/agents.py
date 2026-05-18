"""Multi-agent test pipeline — LangGraph orchestration + ReAct execution."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional

from jsonpath_ng import parse as jsonpath_parse

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool as lc_tool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent
from typing_extensions import TypedDict

from src.testdesigner.config import AppConfig, create_llm
from src.testdesigner.generators import execute_policy, policy_for
from src.testdesigner.models import (
    Assertion,
    BusinessRule,
    CheckResult,
    ConstantVariable,
    EndpointInfo,
    ExecutionReport,
    ExtractedVariable,
    ExtractionRule,
    GeneratedVariable,
    OpenApiCatalog,
    RequestBodyPatch,
    RequestRecord,
    ScenarioCard,
    ScenarioCorrection,
    StepExecution,
    TestStep,
    ToolTrace,
    VariableBinding,
    VariableContext,
    VariableSource,
)
from src.testdesigner.tools import (
    HttpTool,
    collect_refs,
    evaluate_assertions,
    extract_jsonpath,
    find_jsonpath_candidates,
    resolve_templates,
    unresolved_refs,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Prompts
# ──────────────────────────────────────────────────────────────────────────────

PLANNER_PROMPT = """You are Agent 1. Convert a scenario description into a ScenarioCard JSON.

━━━ STEP 1 — COUNT STEPS (STRICT) ━━━
Count every "Endpoint:" line in the scenario text. Output EXACTLY that many steps — no more, no fewer.
- Two "Endpoint:" lines under one business step → two ScenarioCard steps.
- Same endpoint in multiple business steps → ALL steps generated, never deduplicated.
  Example: POST /v1/bookings → 201 in step 2 AND POST /v1/bookings → 409 in step 3 = two separate steps.
- Do NOT invent extra steps not present as "Endpoint:" lines.
- Do NOT merge steps.

━━━ STEP 2 — ASSIGN PATHS ━━━
For each step copy the "path" field from openapi.endpoints VERBATIM.
  ✓ "/v1/accidents/{accidentId}/claims/{claimId}/assessments"
  ✗ "/accidents/claims/assessments"  ← abbreviated — wrong
  ✗ "/v1/accidents/claims"           ← truncated — wrong

━━━ STEP 3 — TEMPLATE SYNTAX (CRITICAL) ━━━
Two syntaxes — never mix:
  path string   → single braces:  "/v1/bookings/{bookingId}/cancel"
  everywhere else → double braces: {"bookingId": "{{bookingId}}"}
Every {param} in path must have a matching key in path_params with a {{variable}} value.
NEVER write {{...}} inside the path string itself.

━━━ STEP 4 — REQUEST BODY VALUES ━━━
PRIORITY RULE — explicit scenario body:
  If the scenario text shows an explicit JSON body for this step (inside a code block or indented block),
  copy those field values VERBATIM:
  - String literals like "user-blocked", "vehicle-nonexistent" → keep as-is (do NOT replace with {{userId}}).
  - Template placeholders like {{vehicleId}}, {{startDate}} → keep as-is.
  Do NOT replace literal values with {{constantName}} just because a constant with that name exists.

Only when the scenario does NOT provide an explicit body, infer it from the OpenAPI example using these rules:

  Rule 1 — constant: look at the "constants" dict in the input.
    If a constant exists whose value or name corresponds to what the scenario needs → use {{constantName}}.
    Example: constants has "userId": "user-1" → write "userId": "{{userId}}" (never copy "user-1" literally).

  Rule 2 — extracted: value must come from a previous response → use {{variableName}}.
    Example: vehicleId from step 1's response → "vehicleId": "{{vehicleId}}"

  Rule 3 — generated: value must be computed at runtime (future dates, unique IDs) → use {{variableName}}.
    Example: booking start date must be in the future → "startDate": "{{startDate}}"

  Rule 4 — scenario literal: the scenario specifies a hardcoded test-specific value that is NOT in constants
    and is NOT meant to be dynamic (e.g., "user-blocked", "vehicle-nonexistent", "non-existent-booking",
    "2020-01-01T00:00:00Z"). Use the literal string exactly as written in the scenario.

  NEVER invent values. NEVER copy OpenAPI example values (like "user-1", "vehicle-1", "2026-05-20T10:00:00Z")
  as string literals — those are placeholders in the spec, not real test values.
  Use enum values stated in the scenario (e.g. role=CULPRIT) instead of the OpenAPI default.

━━━ STEP 5 — PARTICIPANTS & VARIABLE NAMES ━━━
When the same endpoint is called for different roles, use distinct variable names:
  culpritParticipantId  — step that adds CULPRIT
  victimParticipantId   — step that adds VICTIM

━━━ STEP 6 — EXTRACTIONS & DATA FLOW ━━━
- Add an extraction rule for every value a later step needs (IDs, tokens, etc.).
- expression must be a valid JSONPath: "$.id", "$.bookingId", "$.assessmentId".
- For steps with expected_status >= 400: set required=false on all extract rules.
- Do NOT add extraction rules for fields that no later step references.

Return only valid JSON matching the ScenarioCard schema."""


EXECUTOR_PROMPT = """You are an API test executor. Your job: execute an HTTP request and ensure it returns the expected status code.

You have three tools:
  execute_request(body, query_params) — send the HTTP request, returns status + response + matched_expected
  get_endpoint_schema()               — look up the OpenAPI schema and example for this endpoint
  finish(success, message)            — signal you are done; ALWAYS call this last

Workflow:
1. Call execute_request() with the request body provided in the task.
2. If matched_expected is true → call finish(success=True, message="").
3. If matched_expected is false → analyze the error:

   NEVER retry these errors — call finish(success=False, message=reason) immediately:
   • HTTP 404 or error contains NOT_FOUND, DOES_NOT_EXIST — resource doesn't exist.
   • Error contains WRONG_STATE, MUST_BE_IN_, ALREADY_CANCELLED, ALREADY_CONFIRMED,
     INVALID_STATUS — the resource is in the wrong lifecycle state.

   FIXABLE errors — fix the body and retry:
   • EMPTY_BODY, REQUIRED_FIELD, field X is required → call get_endpoint_schema(),
     build a complete body with ALL required fields, retry with execute_request().
   • INVALID_VALUE, wrong enum → fix the specific field and retry.

4. After each retry, check matched_expected again.
5. After 5 failed attempts, call finish(success=False, message=reason).

Always call finish() — the step is not complete until you do."""


# ──────────────────────────────────────────────────────────────────────────────
# Graph State
# ──────────────────────────────────────────────────────────────────────────────

class PipelineState(TypedDict):
    # Inputs
    scenario_text: str
    catalog: OpenApiCatalog
    constants: Dict[str, Any]
    constant_descriptions: Dict[str, str]
    base_url: str
    max_attempts: int
    # Planner output
    scenario_card: Optional[ScenarioCard]
    # Execution state (updated by each step node)
    step_index: int
    variable_context: Optional[VariableContext]
    step_results: List[StepExecution]
    reasoning: List[str]
    traces: List[ToolTrace]
    corrections: List[ScenarioCorrection]


@dataclass
class _StepOutcome:
    """Mutable result shared between ReAct tools and the executor."""

    done: bool = False
    success: bool = False
    message: str = ""
    attempts: List[Dict[str, Any]] = dc_field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Agent 1: Planner
# ──────────────────────────────────────────────────────────────────────────────

class PlannerAgent:
    """Builds a ScenarioCard from scenario text and an OpenAPI catalog.

    Primary path: LLM structured output.
    Fallback: deterministic rule-based card from explicit Endpoint: annotations.
    """

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    # ── Public API ──────────────────────────────────────────────────────────

    def build(
        self,
        scenario_text: str,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
        constant_descriptions: Dict[str, str],
    ) -> ScenarioCard:
        logger.info("Planner: building scenario card")
        endpoints = self._filter_endpoints(scenario_text, catalog)
        if self.llm is not None:
            try:
                prompt = self._build_prompt(scenario_text, endpoints, constants, constant_descriptions)
                structured = self.llm.with_structured_output(ScenarioCard)
                card: ScenarioCard = structured.invoke([
                    ("system", PLANNER_PROMPT),
                    ("human", prompt),
                ])
                logger.info("Planner: LLM generated %d steps", len(card.steps))
                return self._normalize(card, catalog, constants, constant_descriptions)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Planner: LLM failed, falling back to deterministic: %s", exc)
        return self._deterministic_card(scenario_text, catalog, constants, constant_descriptions)

    # ── Prompt helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _filter_endpoints(scenario_text: str, catalog: OpenApiCatalog) -> List[EndpointInfo]:
        pattern = re.compile(r"Endpoint:\s+(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s\n]+)", re.IGNORECASE)
        mentioned = [(m.upper(), p.strip().split("?")[0]) for m, p in pattern.findall(scenario_text)]
        if not mentioned:
            return catalog.endpoints

        def norm(path: str) -> str:
            return re.sub(r"\{[^}]+\}", "{p}", path)

        needed = {(m, norm(p)) for m, p in mentioned}
        filtered = [e for e in catalog.endpoints if (e.method.upper(), norm(e.path)) in needed]
        if filtered:
            logger.info("Planner: using %d/%d endpoints (filtered by scenario)", len(filtered), len(catalog.endpoints))
            return filtered
        return catalog.endpoints

    @staticmethod
    def _build_prompt(
        scenario_text: str,
        endpoints: List[EndpointInfo],
        constants: Dict[str, Any],
        constant_descriptions: Dict[str, str],
    ) -> str:
        endpoint_data = [
            {
                "method": e.method,
                "path": e.path,
                "summary": e.summary,
                "description": e.description,
                "request_example": e.request_example.model_dump(),
                "responses": [r.model_dump(by_alias=True) for r in e.responses],
            }
            for e in endpoints
        ]
        valid_paths = [{"method": e.method, "path": e.path, "summary": e.summary} for e in endpoints]
        return json.dumps(
            {
                "RULE_use_only_these_paths": valid_paths,
                "constants": {
                    name: {"value": constants[name], "description": constant_descriptions.get(name, "")}
                    for name in constants
                },
                "openapi": endpoint_data,
                "scenario_text": scenario_text,
            },
            ensure_ascii=False,
            indent=2,
        )

    # ── Normalization ────────────────────────────────────────────────────────

    def _normalize(
        self,
        card: ScenarioCard,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
        constant_descriptions: Dict[str, str],
    ) -> ScenarioCard:
        # system constants are the only source of truth — discard any LLM-invented
        # constants (they are either wrong values or values that should be generated).
        card.constant_variables = dict(constants)
        card.constant_descriptions = dict(constant_descriptions)
        extracted: Dict[str, tuple[int, str]] = {}
        refs: Dict[str, set[str]] = {}

        for step in card.steps:
            # Paths use single-brace {param} — fix if LLM used double-brace {{param}}.
            step.path = re.sub(r"\{\{(\w+)\}\}", r"{\1}", step.path)
            endpoint = self._find_or_repair_endpoint(step, catalog)
            if endpoint:
                if step.headers is None:
                    step.headers = endpoint.request_example.headers
                step.swagger_operation_id = step.swagger_operation_id or endpoint.operation_id
                step.swagger_notes = step.swagger_notes or {
                    "summary": endpoint.summary,
                    "description": endpoint.description,
                    "comments": endpoint.request_example.comments,
                }
                self._repair_body_nested(step, endpoint)
                self._repair_extraction_paths(step, endpoint)
            # Ensure every path placeholder has a matching path_param entry.
            for param in re.findall(r"\{(\w+)\}", step.path):
                step.path_params.setdefault(param, f"{{{{{param}}}}}")
            for rule in step.extract:
                extracted.setdefault(rule.name, (step.step, rule.expression))
            for name, locs in self._refs_by_location(step).items():
                refs.setdefault(name, set()).update(locs)

        self._fix_single_brace_refs(card)
        self._auto_repair_extractions(card, catalog, refs, extracted, constants)

        card.variable_sources = {}
        for name in sorted(refs):
            if name in card.constant_variables:
                card.variable_sources[name] = VariableSource(
                    name=name,
                    kind="constant",
                    description=card.constant_descriptions.get(name) or "Loaded from constants JSON",
                    locations=sorted(refs[name]),
                    reason="Reference value supplied by constants file.",
                )
            elif name in extracted:
                source_step, expr = extracted[name]
                card.variable_sources[name] = VariableSource(
                    name=name,
                    kind="extracted",
                    locations=sorted(refs[name]),
                    source_step=source_step,
                    extraction_expression=expr,
                    reason="Variable is consumed by a later request and is available in a previous response.",
                )
            else:
                requires: Dict[str, Any] = {}
                if "date" in name.lower():
                    requires = {"format": "iso-date-time", "must_be_future": True}
                card.variable_sources[name] = VariableSource(
                    name=name,
                    kind="generated",
                    locations=sorted(refs[name]),
                    generation_goal=f"Generate valid value for {name}",
                    generation_requires=requires,
                    reason="No constant or prior response extraction is available.",
                )

        for step in card.steps:
            step.variable_bindings = self._bindings_for_step(step, card.variable_sources)

        return card

    def _auto_repair_extractions(
        self,
        card: ScenarioCard,
        catalog: OpenApiCatalog,
        refs: Dict[str, set[str]],
        extracted: Dict[str, tuple[int, str]],
        constants: Dict[str, Any],
    ) -> None:
        """Add missing extraction rules when the planner omitted them."""
        needs = {
            name for name in refs
            if name not in extracted and name not in constants and name.lower().endswith("id")
        }
        if not needs:
            return

        first_usage: Dict[str, int] = {}
        for step in card.steps:
            for name in collect_refs([step.path, step.path_params, step.query_params, step.request_body, step.headers]):
                first_usage.setdefault(name, step.step)

        for var_name in sorted(needs):
            var_lower = var_name.lower()
            first_use = first_usage.get(var_name, len(card.steps) + 1)
            candidate_steps = [s for s in sorted(card.steps, key=lambda s: s.step) if s.step < first_use]
            for step in candidate_steps:
                endpoint = catalog.find(step.method, step.path)
                if endpoint is None or step.method not in ("GET", "POST", "PUT", "PATCH"):
                    continue
                for response in endpoint.responses:
                    if not response.status_code.startswith("2"):
                        continue
                    id_paths = self._id_paths(response.example or {})
                    matched = id_paths.get(var_name)
                    if matched is None:
                        for field, path in id_paths.items():
                            if var_lower.endswith(field.lower()) and len(field) < len(var_name):
                                matched = path
                                break
                    if matched is not None:
                        if not any(r.name == var_name for r in step.extract):
                            step.extract.append(ExtractionRule(
                                name=var_name,
                                expression=matched,
                                required=True,
                                description=f"Auto-repaired extraction for {var_name}",
                            ))
                            extracted[var_name] = (step.step, matched)
                            logger.info(
                                "Planner: auto-repaired extraction %s from step %d (%s)",
                                var_name, step.step, matched,
                            )
                        break

    def _find_or_repair_endpoint(self, step: TestStep, catalog: OpenApiCatalog) -> Optional[EndpointInfo]:
        endpoint = catalog.find(step.method, step.path)
        if endpoint is not None:
            return endpoint

        def strip_prefix(p: str) -> str:
            p = p.strip("/")
            for pfx in ("v1/", "v2/", "v3/", "api/v1/", "api/"):
                if p.startswith(pfx):
                    return p[len(pfx):]
            return p

        step_bare = strip_prefix(step.path)
        for candidate in catalog.endpoints:
            if candidate.method != step.method:
                continue
            if strip_prefix(candidate.path) == step_bare:
                old = step.path
                step.path = candidate.path
                logger.info("Planner: repaired path %r -> %r in step %d", old, step.path, step.step)
                return candidate

        return None

    def _repair_body_nested(self, step: TestStep, endpoint: EndpointInfo) -> None:
        if not isinstance(endpoint.request_example.json_body, dict):
            return
        example = endpoint.request_example.json_body

        # Build missing body from schema only for successful write steps.
        # Error steps (4xx/5xx) need test-specific values that only the planner knows from the scenario.
        if (
            not step.request_body
            and step.method.upper() in ("POST", "PUT", "PATCH")
            and step.expected_status < 400
        ):
            step.request_body = {field: f"{{{{{field}}}}}" for field in example}
            logger.info("Planner: built missing request body for step %d from schema", step.step)
            return

        if not step.request_body:
            return

        # Flatten nested fields that should be top-level.
        openapi_fields = set(example.keys())
        for field in list(step.request_body.keys()):
            if field in openapi_fields:
                continue
            value = step.request_body[field]
            if not isinstance(value, dict):
                continue
            hits = [k for k in value if k in openapi_fields]
            if hits:
                for k in hits:
                    step.request_body[k] = value[k]
                    logger.info("Planner: flattened nested field %r.%r -> %r in step %d", field, k, k, step.step)
                del step.request_body[field]

    def _repair_extraction_paths(self, step: TestStep, endpoint: EndpointInfo) -> None:
        """Validate each extraction rule's JSONPath against the OpenAPI response example.
        If the path doesn't resolve, replace it with the correct path from _id_paths.
        """
        from jsonpath_ng import parse as jsonpath_parse
        for response in endpoint.responses:
            if not response.status_code.startswith("2") or not isinstance(response.example, dict):
                continue
            id_paths = self._id_paths(response.example)
            for rule in step.extract:
                try:
                    matches = jsonpath_parse(rule.expression).find(response.example)
                    if matches:
                        continue  # path works fine
                except Exception:  # noqa: BLE001
                    pass
                # Path doesn't work — look up correct path by variable name
                correct = id_paths.get(rule.name)
                if correct is None:
                    rule_lower = rule.name.lower()
                    for field, path in id_paths.items():
                        if rule_lower.endswith(field.lower()):
                            correct = path
                            break
                if correct and correct != rule.expression:
                    logger.info(
                        "Planner: repaired extraction path %s: %r -> %r in step %d",
                        rule.name, rule.expression, correct, step.step,
                    )
                    rule.expression = correct

    def _fix_single_brace_refs(self, card: ScenarioCard) -> None:
        _single_re = re.compile(r"^\{(\w+)\}$")
        known = set(card.constant_variables) | set(card.variable_sources)

        def fix(v: Any) -> Any:
            if isinstance(v, str):
                m = _single_re.match(v.strip())
                if m:
                    name = m.group(1)
                    if name in known or name.lower().endswith("id"):
                        return "{{" + name + "}}"
                return v
            if isinstance(v, dict):
                return {k: fix(val) for k, val in v.items()}
            if isinstance(v, list):
                return [fix(item) for item in v]
            return v

        for step in card.steps:
            if step.request_body:
                step.request_body = fix(step.request_body)
            if step.path_params:
                step.path_params = {k: fix(v) for k, v in step.path_params.items()}
            if step.query_params:
                step.query_params = fix(step.query_params)
            if step.headers:
                step.headers = fix(step.headers)

    @staticmethod
    def _refs_by_location(step: TestStep) -> Dict[str, set[str]]:
        refs: Dict[str, set[str]] = {}

        def add(value: Any, location: str) -> None:
            for ref in collect_refs(value):
                refs.setdefault(ref, set()).add(location)

        add(step.path, "path")
        add(step.path_params, "path_param")
        add(step.query_params, "query")
        add(step.headers, "header")
        add(step.request_body, "body")
        return refs

    @staticmethod
    def _bindings_for_step(step: TestStep, sources: Dict[str, VariableSource]) -> List[VariableBinding]:
        bindings: List[VariableBinding] = []

        def visit(node: Any, location: str, field_path: str) -> None:
            if isinstance(node, str):
                for ref in collect_refs(node):
                    source = sources.get(ref)
                    if source is None:
                        continue
                    bindings.append(VariableBinding(
                        name=ref,
                        source=source.kind,
                        location=location,  # type: ignore[arg-type]
                        field_path=field_path,
                        description=source.description,
                        generation_goal=source.generation_goal,
                        constraints=source.generation_requires,
                        source_step=source.source_step,
                        extraction_expression=source.extraction_expression,
                        reason=source.reason,
                    ))
            elif isinstance(node, dict):
                for key, value in node.items():
                    visit(value, location, f"{field_path}.{key}" if field_path else key)
            elif isinstance(node, list):
                for idx, value in enumerate(node):
                    visit(value, location, f"{field_path}[{idx}]")

        visit(step.path, "path", "$path")
        visit(step.path_params, "path_param", "$path_params")
        visit(step.query_params, "query", "$query")
        visit(step.headers, "header", "$headers")
        visit(step.request_body, "body", "$body")
        return bindings

    def _id_paths(self, value: Any, prefix: str = "$") -> Dict[str, str]:
        paths: Dict[str, str] = {}
        if isinstance(value, dict):
            for key, item in value.items():
                path = f"{prefix}.{key}"
                if key.lower().endswith("id"):
                    paths.setdefault(key, path)
                paths.update(self._id_paths(item, path))
        elif isinstance(value, list) and value:
            paths.update(self._id_paths(value[0], f"{prefix}[0]"))
        return paths

    # ── Deterministic fallback ───────────────────────────────────────────────

    def _deterministic_card(
        self,
        scenario_text: str,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
        constant_descriptions: Dict[str, str],
    ) -> ScenarioCard:
        steps = self._generic_steps(scenario_text, catalog, constants)
        card = ScenarioCard(
            scenario_name=self._scenario_name(scenario_text),
            business_context=scenario_text[:3000],
            business_rules=self._infer_business_rules(scenario_text),
            constant_variables=constants,
            constant_descriptions=constant_descriptions,
            steps=steps,
        )
        return self._normalize(card, catalog, constants, constant_descriptions)

    def _generic_steps(self, scenario_text: str, catalog: OpenApiCatalog, constants: Dict[str, Any]) -> List[TestStep]:
        explicit = self._steps_from_explicit_http_blocks(scenario_text, catalog, constants)
        if explicit:
            return explicit
        scored = [
            (self._endpoint_score(ep, scenario_text), ep)
            for ep in catalog.endpoints
            if "/debug/" not in ep.path
        ]
        selected = [ep for score, ep in scored if score > 0]
        if not selected:
            selected = [ep for _, ep in sorted(scored, key=lambda x: x[0], reverse=True)[:3]]
        ordered = self._order_by_dependencies(selected)
        ordered = self._add_post_action_verifications(ordered, catalog, scenario_text)
        return [self._step_from_endpoint(i, ep, constants, scenario_text) for i, ep in enumerate(ordered, 1)]

    def _steps_from_explicit_http_blocks(
        self,
        scenario_text: str,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
    ) -> List[TestStep]:
        blocks = self._endpoint_step_blocks(scenario_text)
        if not blocks:
            return []
        steps: List[TestStep] = []
        produced_ids: set[str] = set()
        for block in blocks:
            candidates = [
                ep for ep in catalog.endpoints
                if (block.get("method") is None or ep.method == block["method"]) and "/debug/" not in ep.path
            ]
            if block.get("path"):
                exact = [ep for ep in candidates if ep.path == block["path"]]
                if exact:
                    candidates = exact
            if not candidates:
                continue
            endpoint = max(candidates, key=lambda ep: self._endpoint_block_score(ep, block["text"], block["expected_status"]))
            step = self._step_from_endpoint(
                idx=len(steps) + 1,
                endpoint=endpoint,
                constants=constants,
                scenario_text=block["text"],
                produced_ids=produced_ids,
                expected_status=block["expected_status"],
            )
            self._apply_block_body_overrides(step, block["text"])
            for rule in step.extract:
                produced_ids.add(rule.name)
            steps.append(step)
        return steps

    @staticmethod
    def _endpoint_step_blocks(text: str) -> List[Dict[str, Any]]:
        step_start = re.compile(r"(?im)^(?:#{1,6}\s*)?(?:Шаг|Step)\s*(?P<step>\d+)[^\n]*$")
        endpoint_hint = re.compile(
            r"(?im)^\s*(?:Endpoint|Эндпоинт|API|Запрос)\s*:\s*"
            r"(?:(?P<method>GET|POST|PUT|PATCH|DELETE)\s+)?(?P<path>/[^\s`]+)\s*$"
        )
        blocks: List[Dict[str, Any]] = []
        matches = list(step_start.finditer(text))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            section = text[match.start():end].strip()
            hints = [
                {"method": h.group("method").upper() if h.group("method") else None, "path": h.group("path")}
                for h in endpoint_hint.finditer(section)
            ]
            if not hints:
                continue
            statuses = [int(s) for s in re.findall(r"\b(20\d|40\d|50\d)\b", section)]
            for idx, hint in enumerate(hints):
                blocks.append({
                    "step": int(match.group("step")),
                    "method": hint["method"],
                    "path": hint["path"],
                    "expected_status": statuses[idx] if idx < len(statuses) else None,
                    "text": section,
                })
        return blocks

    def _apply_block_body_overrides(self, step: TestStep, block_text: str) -> None:
        if not step.request_body:
            return
        for field, raw_value in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*([A-Za-z][A-Za-z0-9_]*|\d+(?:[.,]\d+)?)", block_text):
            if field in step.request_body:
                value: Any = raw_value.replace(",", ".")
                if re.fullmatch(r"\d+(?:\.\d+)?", value):
                    value = float(value) if "." in value else int(value)
                step.request_body[field] = value

    def _endpoint_score(self, endpoint: EndpointInfo, scenario_text: str) -> int:
        text_tokens = self._text_tokens(scenario_text)
        score = 0
        for token in self._endpoint_tokens(endpoint):
            token_l = token.lower()
            if len(token_l) < 3:
                continue
            if token_l in text_tokens:
                score += 3 if token_l.endswith("id") else 1
            if token_l.endswith("s") and token_l[:-1] in text_tokens:
                score += 1
        primary = self._primary_resource(endpoint.path)
        if score > 0 and primary and primary not in text_tokens and primary.rstrip("s") not in text_tokens:
            if len(endpoint.path.strip("/").split("/")) > 3:
                return 0
        return score

    def _endpoint_block_score(self, endpoint: EndpointInfo, block_text: str, expected_status: Optional[int]) -> int:
        score = self._endpoint_score(endpoint, block_text)
        if expected_status is not None:
            statuses = {int(r.status_code) for r in endpoint.responses if r.status_code.isdigit()}
            score += 8 if expected_status in statuses else -8
        body_keys = set(endpoint.request_example.json_body.keys()) if isinstance(endpoint.request_example.json_body, dict) else set()
        text = block_text.lower()
        for key in body_keys:
            if key.lower() in text:
                score += 6
        return score

    def _step_from_endpoint(
        self,
        idx: int,
        endpoint: EndpointInfo,
        constants: Dict[str, Any],
        scenario_text: str,
        produced_ids: Optional[set[str]] = None,
        expected_status: Optional[int] = None,
    ) -> TestStep:
        body = endpoint.request_example.json_body if isinstance(endpoint.request_example.json_body, dict) else None
        if body:
            body = {k: self._template_value(k, v, constants, produced_ids or set()) for k, v in body.items()}
        query = {k: self._template_value(k, v, constants) for k, v in endpoint.request_example.query_params.items()} or None
        status = expected_status or self._preferred_status(endpoint)
        return TestStep(
            step=idx,
            name=endpoint.summary or f"{endpoint.method} {endpoint.path}",
            method=endpoint.method,
            path=endpoint.path,
            query_params=query,
            request_body=body,
            path_params={k: f"{{{{{k}}}}}" for k in re.findall(r"\{(\w+)\}", endpoint.path)},
            expected_status=status,
            success_criteria=[endpoint.summary or "request completed"],
            extract=self._infer_extractions(endpoint, status),
            assertions=self._infer_assertions(endpoint, status, scenario_text),
            swagger_operation_id=endpoint.operation_id,
            swagger_notes={
                "summary": endpoint.summary,
                "description": endpoint.description,
                "comments": endpoint.request_example.comments,
            },
        )

    @staticmethod
    def _template_value(name: str, value: Any, constants: Dict[str, Any], produced_ids: Optional[set[str]] = None) -> Any:
        if name in constants:
            return f"{{{{{name}}}}}"
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        if snake in constants:
            return f"{{{{{snake}}}}}"
        if produced_ids and name.lower().endswith("id"):
            for produced in sorted(produced_ids, key=len, reverse=True):
                if name.lower().endswith(produced.lower()):
                    return f"{{{{{produced}}}}}"
        if name.lower().endswith("id") or "date" in name.lower():
            return f"{{{{{name}}}}}"
        return value

    @staticmethod
    def _preferred_status(endpoint: EndpointInfo) -> int:
        for r in endpoint.responses:
            if r.status_code.startswith("2"):
                return int(r.status_code)
        return int(endpoint.responses[0].status_code) if endpoint.responses else 200

    def _infer_extractions(self, endpoint: EndpointInfo, status: int) -> List[ExtractionRule]:
        response = self._response_for_status(endpoint, status)
        return [
            ExtractionRule(name=name, expression=path)
            for name, path in self._id_paths(response.example if response else None).items()
        ]

    def _infer_assertions(self, endpoint: EndpointInfo, status: int, scenario_text: str) -> List[Assertion]:
        response = self._response_for_status(endpoint, status)
        example = response.example if response else None
        if isinstance(example, (dict, list)):
            for name, path in self._id_paths(example).items():
                return [Assertion(description=f"{name} is present", path=path, operator="not_null")]
        return []

    @staticmethod
    def _response_for_status(endpoint: EndpointInfo, status: int) -> Any:
        for r in endpoint.responses:
            if r.status_code == str(status):
                return r
        for r in endpoint.responses:
            if r.status_code.startswith("2"):
                return r
        return endpoint.responses[0] if endpoint.responses else None

    def _order_by_dependencies(self, endpoints: List[EndpointInfo]) -> List[EndpointInfo]:
        def priority(ep: EndpointInfo) -> tuple[int, int, int, str]:
            params = len(re.findall(r"\{(\w+)\}", ep.path))
            method_rank = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 2, "DELETE": 3}.get(ep.method, 9)
            depth = max(0, len(ep.path.strip("/").split("/")) - params - 2)
            return (params, method_rank, depth, ep.path)
        return sorted(endpoints, key=priority)

    def _add_post_action_verifications(
        self,
        endpoints: List[EndpointInfo],
        catalog: OpenApiCatalog,
        scenario_text: str,
    ) -> List[EndpointInfo]:
        if not any(w in scenario_text.lower() for w in ("check", "verify", "final", "status", "провер", "статус")):
            return endpoints
        result: List[EndpointInfo] = []
        for ep in endpoints:
            result.append(ep)
            if ep.method not in {"POST", "PUT", "PATCH", "DELETE"}:
                continue
            parent = "/" + "/".join(ep.path.strip("/").split("/")[:-1])
            verifier = catalog.find("GET", parent)
            if verifier is not None:
                result.append(verifier)
        return result

    @staticmethod
    def _text_tokens(text: str) -> set[str]:
        raw = set(re.findall(r"[A-Za-z][A-Za-z0-9_]+", text))
        tokens: set[str] = set()
        for token in raw:
            tokens.add(token.lower())
            tokens.update(p.lower() for p in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token))
        return tokens

    @staticmethod
    def _primary_resource(path: str) -> Optional[str]:
        segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
        if segments and re.fullmatch(r"v\d+", segments[0], re.IGNORECASE):
            segments = segments[1:]
        return segments[0].lower() if segments else None

    def _endpoint_tokens(self, endpoint: EndpointInfo) -> set[str]:
        raw = set(re.findall(r"[A-Za-z][A-Za-z0-9_]+", endpoint.path))
        raw.update(re.findall(r"[A-Za-z][A-Za-z0-9_]+", endpoint.operation_id or ""))
        raw.update(re.findall(r"[A-Za-z][A-Za-z0-9_]+", endpoint.summary or ""))
        raw.update(re.findall(r"[A-Za-z][A-Za-z0-9_]+", endpoint.description or ""))
        if isinstance(endpoint.request_example.json_body, dict):
            raw.update(endpoint.request_example.json_body.keys())
        for r in endpoint.responses:
            raw.update(self._json_keys(r.example))
        tokens: set[str] = set()
        for token in raw:
            tokens.add(token)
            tokens.update(p for p in re.split(r"[_\-/]", token) if p)
            tokens.update(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token))
        return tokens

    def _json_keys(self, value: Any) -> set[str]:
        keys: set[str] = set()
        if isinstance(value, dict):
            for k, v in value.items():
                keys.add(k)
                keys |= self._json_keys(v)
        elif isinstance(value, list):
            for item in value:
                keys |= self._json_keys(item)
        return keys

    @staticmethod
    def _infer_business_rules(text: str) -> List[BusinessRule]:
        rules: List[BusinessRule] = []
        if "future" in text.lower() or "будущ" in text.lower():
            rules.append(BusinessRule(id="BR-001", description="Date-like generated values must be in the future."))
        return rules

    @staticmethod
    def _scenario_name(text: str) -> str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "Generated API scenario")
        return first[:120]


# ──────────────────────────────────────────────────────────────────────────────
# Agent 2: Executor (ReAct)
# ──────────────────────────────────────────────────────────────────────────────

class ExecutorAgent:
    """Executes scenario steps one at a time using a ReAct agent for error analysis and retry."""

    def __init__(self, llm: Any, http: HttpTool) -> None:
        self.llm = llm
        self.http = http

    def execute_step(
        self,
        step: TestStep,
        card: ScenarioCard,
        context: VariableContext,
        base_url: str,
        catalog: Optional[OpenApiCatalog],
        max_attempts: int,
    ) -> tuple[StepExecution, VariableContext]:
        """Execute one step. Returns (StepExecution result, updated variable context)."""
        logger.info(
            "Executor: step %d/%s — %s %s (expected %s)",
            step.step, step.name, step.method, step.path, step.expected_status,
        )
        context = self._prepare_variables(step, card, context)
        endpoint_info = catalog.find(step.method, step.path) if catalog else None
        outcome = _StepOutcome()

        tools = self._make_tools(step, context, base_url, endpoint_info, outcome)
        initial_msg = self._build_step_message(step, context, base_url)

        agent = create_react_agent(self.llm, tools, prompt=EXECUTOR_PROMPT)
        try:
            agent.invoke(
                {"messages": [HumanMessage(content=initial_msg)]},
                config={"recursion_limit": max_attempts * 4 + 10},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Executor: ReAct error on step %d: %s", step.step, exc)
            if not outcome.done:
                outcome.done = True
                outcome.success = False
                outcome.message = str(exc)

        # Fallback: if agent didn't call finish(), infer from last attempt.
        if not outcome.done and outcome.attempts:
            last = outcome.attempts[-1]
            outcome.done = True
            outcome.success = last.get("status_code") == step.expected_status
            outcome.message = "" if outcome.success else f"Agent did not call finish(). Last status: {last.get('status_code')}"

        # Extract variables from the successful response.
        if outcome.success and outcome.attempts:
            last_response = outcome.attempts[-1].get("response_body")
            if last_response is not None:
                extracted, errors = extract_jsonpath(last_response, step.extract)
                if errors:
                    extracted = self._repair_extractions(step, last_response, extracted, errors)
                for rule in step.extract:
                    if rule.name in extracted:
                        old = context.extracted.get(rule.name)
                        context.extracted[rule.name] = ExtractedVariable(
                            name=rule.name,
                            extracted_value=extracted[rule.name],
                            source_step=step.step,
                            extraction_expression=rule.expression,
                            overwrite_reason=f"Overwrote value from step {old.source_step}" if old else None,
                        )
                        logger.info("Executor: extracted %s=%r", rule.name, extracted[rule.name])

        return self._build_step_execution(step, outcome), context

    # ── Variable preparation ─────────────────────────────────────────────────

    def _prepare_variables(self, step: TestStep, card: ScenarioCard, context: VariableContext) -> VariableContext:
        values = context.values()
        missing = unresolved_refs(
            [step.path, step.path_params, step.query_params, step.request_body, step.headers],
            values,
        )
        for name in sorted(missing):
            source = card.variable_sources.get(name)
            if source and source.kind == "extracted":
                logger.warning("Executor: variable %s should be extracted but is not yet available", name)
                continue
            policy = policy_for(name, None)
            value = execute_policy(policy, values, None)
            context.generated[name] = GeneratedVariable(
                name=name,
                generated_value=value,
                generator_name=policy.name,
                generator_params=source.generation_requires if source else {},
                reason=source.generation_goal if source else f"Auto-generated for {name}",
                generator_function=policy.generator_function,
            )
            logger.info("Executor: generated %s=%r", name, value)
        return context

    # ── ReAct tools ──────────────────────────────────────────────────────────

    def _make_tools(
        self,
        step: TestStep,
        context: VariableContext,
        base_url: str,
        endpoint_info: Optional[EndpointInfo],
        outcome: _StepOutcome,
    ) -> List[Any]:
        """Create per-step tools as closures over the execution context."""
        http = self.http
        values = context.values()

        # Resolve path and query params once — they don't change between retries.
        resolved_path = resolve_templates(step.path, values)
        for key, val in step.path_params.items():
            resolved_path = resolved_path.replace("{" + key + "}", str(resolve_templates(val, values)))
        url = base_url.rstrip("/") + resolved_path

        resolved_headers = resolve_templates(
            step.headers or {"Content-Type": "application/json", "Accept": "application/json"},
            values,
        )
        resolved_query = resolve_templates(step.query_params, values) if step.query_params else None
        initial_body = resolve_templates(step.request_body, values) if step.request_body else None

        expected = step.expected_status

        @lc_tool
        def execute_request(
            body: Optional[Dict[str, Any]] = None,
            query_params: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            """Execute the HTTP request for this step.

            Args:
                body: Request body dict. Pass None for bodyless requests (GET).
                      If not provided, uses the body from the scenario plan.
                query_params: Override query parameters. Uses plan defaults if None.

            Returns dict with: status (int), response (dict/str),
                                matched_expected (bool), error (str or None).
            """
            effective_body = body if body is not None else initial_body
            effective_query = query_params if query_params is not None else resolved_query

            status, response, err = http.request(step.method, url, resolved_headers, effective_query, effective_body)
            matched = (status == expected) and err is None

            outcome.attempts.append({
                "method": step.method,
                "url": url,
                "body": effective_body,
                "status_code": status,
                "response_body": response,
                "error": err,
            })
            logger.info("Executor tool: %s %s -> %s (expected %s)", step.method, url, status, expected)

            return {
                "status": status,
                "response": response,
                "matched_expected": matched,
                "expected_status": expected,
                "error": err,
            }

        @lc_tool
        def get_endpoint_schema() -> Dict[str, Any]:
            """Return the OpenAPI schema and request/response examples for this endpoint.
            Use this to look up correct field names and required fields when the server
            returns a validation error.
            """
            if endpoint_info is None:
                return {"error": "Schema not available for this endpoint."}
            return {
                "method": endpoint_info.method,
                "path": endpoint_info.path,
                "summary": endpoint_info.summary,
                "request_example": endpoint_info.request_example.json_body,
                "responses": {r.status_code: r.example for r in endpoint_info.responses},
            }

        @lc_tool
        def finish(success: bool, message: str = "") -> str:
            """Signal that this step execution is complete. Always call this last.

            Args:
                success: True if the expected HTTP status was received, False otherwise.
                message: Explanation of the outcome, especially on failure.
            """
            outcome.done = True
            outcome.success = success
            outcome.message = message
            status_word = "passed" if success else "failed"
            logger.info("Executor tool: finish(success=%s) step %d — %s", success, step.step, message or status_word)
            return f"Step {status_word}: {message}"

        return [execute_request, get_endpoint_schema, finish]

    # ── Result building ──────────────────────────────────────────────────────

    def _build_step_message(self, step: TestStep, context: VariableContext, base_url: str) -> str:
        values = context.values()
        resolved_path = resolve_templates(step.path, values)
        for key, val in step.path_params.items():
            resolved_path = resolved_path.replace("{" + key + "}", str(resolve_templates(val, values)))

        return json.dumps(
            {
                "task": f"Execute step {step.step}: {step.name}",
                "method": step.method,
                "url": base_url.rstrip("/") + resolved_path,
                "expected_status": step.expected_status,
                "request_body": resolve_templates(step.request_body, values) if step.request_body else None,
                "query_params": resolve_templates(step.query_params, values) if step.query_params else None,
                "extract_on_success": [
                    {"variable": r.name, "jsonpath": r.expression}
                    for r in step.extract if r.required
                ],
                "notes": step.notes or step.swagger_notes.get("summary", ""),
            },
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _repair_extractions(
        step: TestStep,
        response_body: Any,
        extracted: Dict[str, Any],
        errors: List[str],
    ) -> Dict[str, Any]:
        """Try to find correct JSONPath when Agent 1's expression didn't match."""
        failed_names = {err.split(":")[0].strip() for err in errors if ":" in err}
        for rule in step.extract:
            if rule.name not in failed_names:
                continue
            candidates = find_jsonpath_candidates(response_body, rule.name)
            if not candidates:
                continue
            old = rule.expression
            for path in candidates:
                try:
                    matches = jsonpath_parse(path).find(response_body)
                    if matches:
                        rule.expression = path
                        extracted[rule.name] = matches[0].value
                        logger.info("Executor: repaired extraction %s: %r -> %r (value=%r)", rule.name, old, path, matches[0].value)
                        break
                except Exception:  # noqa: BLE001
                    continue
        return extracted

    def _build_step_execution(self, step: TestStep, outcome: _StepOutcome) -> StepExecution:
        history: List[RequestRecord] = []
        for i, attempt in enumerate(outcome.attempts):
            history.append(RequestRecord(
                step=step.step,
                attempt=i + 1,
                name=step.name,
                method=step.method,
                url=attempt["url"],
                templated_path=step.path,
                templated_request_body=step.request_body,
                request_body=attempt["body"],
                response_status=attempt["status_code"],
                response_body=attempt["response_body"],
                checks=[CheckResult(
                    description=f"HTTP {attempt['status_code']} == {step.expected_status}",
                    passed=(attempt["status_code"] == step.expected_status),
                    actual=attempt["status_code"],
                    expected=step.expected_status,
                )],
            ))

        status = "passed" if outcome.success else ("failed" if outcome.done else "skipped")
        # Attach extraction rules (with runtime-repaired expressions) and assertions to the
        # final record so the Postman collection generator can build correct test scripts.
        if history and outcome.success:
            history[-1].extract = list(step.extract)
            history[-1].assertions = list(step.assertions)
        return StepExecution(
            step=step.step,
            name=step.name,
            status=status,
            error=None if outcome.success else outcome.message or "Step failed",
            attempt_history=history,
            final_request=history[-1] if history else None,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline Graph
# ──────────────────────────────────────────────────────────────────────────────

class PipelineGraph:
    """LangGraph pipeline: plan → execute steps (loop) → finalize."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.planner = PlannerAgent(create_llm(config.llm_agent1 or config.llm))
        self.http = HttpTool(timeout_seconds=config.runtime.request_timeout_seconds)
        self.executor = ExecutorAgent(
            llm=create_llm(config.llm_agent2 or config.llm),
            http=self.http,
        )
        self._graph = self._build_graph()

    def close(self) -> None:
        self.http.close()

    # ── Public API ───────────────────────────────────────────────────────────

    def run(
        self,
        scenario_text: str,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
        constant_descriptions: Dict[str, str],
        base_url: str,
        execute: bool = True,
    ) -> tuple[ScenarioCard, Optional[ExecutionReport]]:
        """Run the full pipeline. Returns (ScenarioCard, ExecutionReport or None)."""
        if not execute:
            card = self.planner.build(scenario_text, catalog, constants, constant_descriptions)
            return card, None

        initial: PipelineState = {
            "scenario_text": scenario_text,
            "catalog": catalog,
            "constants": constants,
            "constant_descriptions": constant_descriptions,
            "base_url": base_url,
            "max_attempts": self.config.runtime.max_attempts_per_step,
            "scenario_card": None,
            "step_index": 0,
            "variable_context": None,
            "step_results": [],
            "reasoning": [],
            "traces": [],
            "corrections": [],
        }

        logger.info("Pipeline: starting graph execution")
        final = self._graph.invoke(initial)
        report = self._build_report(final)
        logger.info("Pipeline: complete — status=%s", report.status)
        return final["scenario_card"], report

    # ── Graph nodes ──────────────────────────────────────────────────────────

    def _plan_node(self, state: PipelineState) -> dict:
        logger.info("Graph: plan node")
        card = self.planner.build(
            state["scenario_text"],
            state["catalog"],
            state["constants"],
            state["constant_descriptions"],
        )
        ctx = VariableContext(constants={
            name: ConstantVariable(
                name=name,
                value=value,
                description=card.constant_descriptions.get(name) or "From constants JSON",
            )
            for name, value in card.constant_variables.items()
        })
        step_names = [f"Step {s.step}: {s.name}" for s in card.steps]
        logger.info("Graph: planned %d steps — %s", len(card.steps), step_names)
        return {
            "scenario_card": card,
            "variable_context": ctx,
            "reasoning": [f"Planned {len(card.steps)} steps: {step_names}"],
        }

    def _execute_step_node(self, state: PipelineState) -> dict:
        card = state["scenario_card"]
        idx = state["step_index"]
        step = card.steps[idx].model_copy(deep=True)
        context = state["variable_context"].model_copy(deep=True)

        logger.info("Graph: executing step %d/%d", idx + 1, len(card.steps))

        step_exec, updated_ctx = self.executor.execute_step(
            step=step,
            card=card,
            context=context,
            base_url=state["base_url"],
            catalog=state["catalog"],
            max_attempts=state["max_attempts"],
        )

        reasoning_entry = (
            f"Step {step.step} ({step.name}): {step_exec.status}"
            + (f" — {step_exec.error}" if step_exec.error else "")
        )
        return {
            "step_index": idx + 1,
            "step_results": state["step_results"] + [step_exec],
            "variable_context": updated_ctx,
            "reasoning": state["reasoning"] + [reasoning_entry],
        }

    def _finalize_node(self, state: PipelineState) -> dict:
        card = state["scenario_card"]
        executed = {r.step for r in state["step_results"]}
        skipped = [
            StepExecution(step=s.step, name=s.name, status="skipped", error="Previous step failed")
            for s in card.steps if s.step not in executed
        ]
        all_results = state["step_results"] + skipped
        status = "passed" if all(r.status == "passed" for r in all_results) else "failed"
        logger.info("Graph: finalize — %s", status)
        return {"step_results": all_results}

    # ── Graph routing ────────────────────────────────────────────────────────

    def _router(self, state: PipelineState) -> str:
        last = state["step_results"][-1] if state["step_results"] else None
        if last and last.status == "failed":
            logger.info("Graph: step %d failed — terminating early", last.step)
            return "finalize"
        if state["step_index"] >= len(state["scenario_card"].steps):
            return "finalize"
        return "execute_step"

    # ── Report builder ───────────────────────────────────────────────────────

    def _build_report(self, state: PipelineState) -> ExecutionReport:
        results = state["step_results"]
        card = state["scenario_card"]
        successful = [r.final_request for r in results if r.status == "passed" and r.final_request]
        return ExecutionReport(
            scenario_name=card.scenario_name if card else "unknown",
            status="passed" if all(r.status == "passed" for r in results) else "failed",
            reasoning=state["reasoning"],
            steps=results,
            successful_requests=successful,
            variables=state["variable_context"] or VariableContext(),
            corrections=state["corrections"],
            traces=state["traces"],
        )

    # ── Graph builder ────────────────────────────────────────────────────────

    def _build_graph(self) -> Any:
        g: StateGraph = StateGraph(PipelineState)
        g.add_node("plan", self._plan_node)
        g.add_node("execute_step", self._execute_step_node)
        g.add_node("finalize", self._finalize_node)
        g.set_entry_point("plan")
        g.add_edge("plan", "execute_step")
        g.add_conditional_edges(
            "execute_step",
            self._router,
            {"execute_step": "execute_step", "finalize": "finalize"},
        )
        g.add_edge("finalize", END)
        return g.compile()
