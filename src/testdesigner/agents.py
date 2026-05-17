"""Multi-agent implementation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

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
    ScenarioCorrection,
    ScenarioCard,
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

AGENT1_SYSTEM_PROMPT = """You are Agent 1. Your job: convert a scenario description into a ScenarioCard JSON.

━━━ STEP 1 — COUNT STEPS ━━━
Count every "Endpoint:" line in the scenario text. Each line becomes exactly ONE ScenarioCard step.
- Two "Endpoint:" lines under one business step → two ScenarioCard steps, sequential numbers.
- Same endpoint (same method + path) appearing in multiple business steps → generate ALL steps, one per occurrence.
  Never deduplicate. POST /v1/bookings → 201 in step 2 AND POST /v1/bookings → 409 in step 3 = two separate steps.

━━━ STEP 2 — ASSIGN PATHS ━━━
For each step, find the matching entry in openapi.endpoints by reading its summary/description.
Copy the "path" field VERBATIM. No abbreviations, no paraphrasing, no invented paths.
  ✓ "/v1/accidents/{accidentId}/claims/{claimId}/assessments"
  ✗ "/accidents/claims/assessments"  ← wrong
  ✗ "/v1/accidents/claims"           ← wrong

━━━ STEP 3 — TEMPLATE SYNTAX ━━━
Two different syntaxes — never mix them:

  path string      → single braces for OpenAPI placeholders:
                     "/v1/bookings/{bookingId}/cancel"

  everywhere else  → double braces for runtime variables:
                     path_params : {"bookingId": "{{bookingId}}"}
                     request_body: {"userId": "{{user_id}}", "vehicleId": "{{vehicleId}}"}
                     query_params: {"city": "{{city}}"}

  Rule: NEVER write {{...}} inside the path string. NEVER write {singleBrace} in body/params.
  Every {placeholder} in the path string MUST have a matching key in path_params.

━━━ STEP 4 — REQUEST BODY ━━━
- Field names come from the OpenAPI example (openapi.endpoints[].request_example), not from scenario text.
- Use enum values stated in the scenario (e.g. role=CULPRIT, role=VICTIM), not the OpenAPI example default.
- When the scenario states a LITERAL value, write it as a plain string — do NOT wrap in {{}}:
    "userId": "user-blocked"           ← literal, stay as-is
    "startDate": "2020-01-01T00:00:00Z" ← literal, stay as-is
    "vehicleId": "vehicle-nonexistent" ← literal, stay as-is
- When the value comes from a previous response or a constant, use {{variableName}}.

━━━ STEP 5 — PARTICIPANTS & VARIABLE NAMES ━━━
When the same endpoint is called for different participants (e.g. two POST /participants calls),
give each participant a distinct variable name so their IDs do not overwrite each other:
  culpritParticipantId  (step that adds CULPRIT)
  victimParticipantId   (step that adds VICTIM)
Use those distinct names in all downstream path_params and request_body references.

━━━ STEP 6 — EXTRACTIONS & DATA FLOW ━━━
- Add an extraction rule for every value a later step needs (IDs, tokens, etc.).
- expression must be a valid JSONPath: "$.id", "$.bookingId", "$.assessmentId".
- For steps with expected_status >= 400: the request is expected to FAIL.
  Set required=false on all extract rules in that step (the response may not contain useful data).
- Do NOT add extraction rules for fields that no later step references.

Return only valid JSON matching the ScenarioCard schema."""

AGENT2_ERROR_ANALYSIS_PROMPT = """You are Agent 2. A REST API request has failed. Decide whether the error can be fixed
by changing the request body, and if so, return the minimal patch.

━━━ DECISION TREE ━━━

NEVER fixable by body patch — return fixable=false immediately for these cases:
  • HTTP 404 — the resource does not exist; a body change cannot create it.
  • Error codes containing NOT_FOUND, DOES_NOT_EXIST — same reason.
  • Error codes containing state/status mismatch (WRONG_STATE, MUST_BE_IN_, INVALID_STATUS,
    ALREADY_CANCELLED, ALREADY_CONFIRMED) — the resource is in the wrong lifecycle state;
    body changes cannot fix the state machine. A prerequisite step must run first.
  • Any error that says "resource X must be in state Y" — not fixable by body.

FIXABLE by body patch — return fixable=true:
  • Missing required field (EMPTY_BODY, REQUIRED_FIELD, MISSING_FIELD, field X is required).
  • Wrong field value (INVALID_VALUE, wrong enum, wrong format).
  • Body was null/empty and the server wants a populated body.

━━━ HOW TO BUILD body_patch ━━━
1. Read the error code and message carefully — they name the problematic field.
2. Use the exact field name the SERVER expects (from the error message or openapi_context.expected_request_example).
3. Keys: TOP-LEVEL field names only. No nesting, no dot-notation.
4. Values: scalar (string, number, bool) or flat list. Never a nested dict.
5. For null/empty body: return ALL required fields from openapi_context.expected_request_example.
   Adjust enum values (e.g. role) using step_name context — "Add victim" → role=VICTIM.
6. For existing body: return ONLY the field(s) needed to fix this specific error.
7. Prefer values from extracted_context (real IDs from previous steps) over invented values.

━━━ REASONING ━━━
Always explain: what the error means, why fixable or not, what you changed and why.
"""

AGENT3_SYSTEM_PROMPT = """You are Agent 3, a data-generation specialist.
Return generation policies that satisfy format and business constraints. Prefer
deterministic, reviewable Python expressions."""


class ScenarioBuilderAgent:
    """Agent 1: creates a scenario card.

    The primary path is LLM structured output. The deterministic fallback is
    deliberately domain-agnostic: it scores OpenAPI operations against the
    scenario text and derives templates/extractions/assertions from schemas and
    response examples.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.llm = create_llm(config.llm_agent1 or config.llm)

    def build(
        self,
        scenario_text: str,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
        constant_descriptions: Optional[Dict[str, str]] = None,
    ) -> ScenarioCard:
        logger.info("Agent1: build scenario card")
        constant_descriptions = constant_descriptions or {}
        if self.llm is not None:
            try:
                structured = self.llm.with_structured_output(ScenarioCard)
                card = structured.invoke(
                    [
                        ("system", AGENT1_SYSTEM_PROMPT),
                        ("human", self._prompt(scenario_text, catalog, constants, constant_descriptions)),
                    ]
                )
                return self._normalize(card, catalog, constants, constant_descriptions)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Agent1: LLM build failed, using generic deterministic fallback: %s", exc)
        return self._deterministic_card(scenario_text, catalog, constants, constant_descriptions)

    @staticmethod
    def _filter_catalog_for_scenario(scenario_text: str, catalog: OpenApiCatalog):
        """Return only catalog endpoints that are referenced in the scenario text."""
        pattern = re.compile(r"Endpoint:\s+(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s\n]+)", re.IGNORECASE)
        mentioned = [(m.upper(), p.strip()) for m, p in pattern.findall(scenario_text)]
        if not mentioned:
            return catalog.endpoints

        def _norm(path: str) -> str:
            path = path.split("?")[0]  # strip query string
            return re.sub(r"\{[^}]+\}", "{p}", path)

        needed = {(method, _norm(path)) for method, path in mentioned}
        filtered = [e for e in catalog.endpoints if (e.method.upper(), _norm(e.path)) in needed]
        if not filtered:
            return catalog.endpoints
        logger.info("Agent1: using %d/%d endpoints from catalog (filtered by scenario)", len(filtered), len(catalog.endpoints))
        return filtered

    def _prompt(
        self,
        scenario_text: str,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
        constant_descriptions: Dict[str, str],
    ) -> str:
        relevant = self._filter_catalog_for_scenario(scenario_text, catalog)
        endpoints = [
            {
                "method": e.method,
                "path": e.path,
                "summary": e.summary,
                "description": e.description,
                "request_example": e.request_example.model_dump(),
                "responses": [r.model_dump(by_alias=True) for r in e.responses],
            }
            for e in relevant
        ]
        valid_paths = [{"method": e.method, "path": e.path, "summary": e.summary} for e in relevant]
        return json.dumps(
            {
                "RULE_use_only_these_paths": valid_paths,
                "constants": {
                    name: {"value": value, "description": constant_descriptions.get(name, "")}
                    for name, value in constants.items()
                },
                "openapi": endpoints,
                "scenario_text": scenario_text,
            },
            ensure_ascii=False,
            indent=2,
        )

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
        explicit_steps = self._steps_from_explicit_http_blocks(scenario_text, catalog, constants)
        if explicit_steps:
            return explicit_steps

        scored = [
            (self._endpoint_score(endpoint, scenario_text), endpoint)
            for endpoint in catalog.endpoints
            if "/debug/" not in endpoint.path
        ]
        selected = [endpoint for score, endpoint in scored if score > 0]
        if not selected:
            selected = [endpoint for _, endpoint in sorted(scored, key=lambda item: item[0], reverse=True)[:3]]
        ordered = self._order_by_dependencies(selected)
        ordered = self._add_post_action_verifications(ordered, catalog, scenario_text)
        return [
            self._step_from_endpoint(idx, endpoint, constants, scenario_text)
            for idx, endpoint in enumerate(ordered, start=1)
        ]

    def _steps_from_explicit_http_blocks(
        self,
        scenario_text: str,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
    ) -> List[TestStep]:
        blocks = self._endpoint_step_blocks(scenario_text) or self._http_step_blocks(scenario_text)
        if not blocks:
            return []
        steps: List[TestStep] = []
        produced_ids: set[str] = set()
        for block in blocks:
            candidates = [
                endpoint for endpoint in catalog.endpoints
                if (block.get("method") is None or endpoint.method == block["method"]) and "/debug/" not in endpoint.path
            ]
            if block.get("path"):
                exact = [endpoint for endpoint in candidates if endpoint.path == block["path"]]
                if exact:
                    candidates = exact
            if not candidates:
                continue
            endpoint = max(candidates, key=lambda item: self._endpoint_block_score(item, block["text"], block["expected_status"]))
            step = self._step_from_endpoint(
                idx=len(steps) + 1,
                endpoint=endpoint,
                constants=constants,
                scenario_text=block["text"],
                produced_ids=produced_ids,
                expected_status=block["expected_status"],
            )
            self._apply_block_body_overrides(step, block["text"])
            self._apply_block_extraction_aliases(step, block["text"])
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
                {
                    "method": hint.group("method").upper() if hint.group("method") else None,
                    "path": hint.group("path"),
                }
                for hint in endpoint_hint.finditer(section)
            ]
            if not hints:
                continue
            statuses = [int(item) for item in re.findall(r"\b(20\d|40\d|50\d)\b", section)]
            for idx, hint in enumerate(hints):
                blocks.append(
                    {
                        "step": int(match.group("step")),
                        "method": hint["method"],
                        "path": hint["path"],
                        "expected_status": statuses[idx] if idx < len(statuses) else None,
                        "text": section,
                    }
                )
        return blocks

    @staticmethod
    def _http_step_blocks(text: str) -> List[Dict[str, Any]]:
        pattern = re.compile(
            r"(?P<header>#{1,6}\s*[^\n]*?(?:Шаг|Step)\s*(?P<step>\d+)[^\n]*?HTTP\s+"
            r"(?P<method>GET|POST|PUT|PATCH|DELETE)[^\n]*?(?P<status>\d{3})?[^\n]*\n)"
            r"(?P<body>.*?)(?=\n#{1,6}\s*[^\n]*?(?:Шаг|Step)\s*\d+[^\n]*?HTTP\s+|$)",
            flags=re.IGNORECASE | re.DOTALL,
        )
        blocks: List[Dict[str, Any]] = []
        for match in pattern.finditer(text):
            header = match.group("header")
            body = match.group("body")
            header_line = header.splitlines()[0]
            methods = [item.upper() for item in re.findall(r"\b(?:HTTP\s*)?(GET|POST|PUT|PATCH|DELETE)\b", header_line, flags=re.IGNORECASE)]
            statuses = [int(item) for item in re.findall(r"\b(20\d|40\d|50\d)\b", header_line)]
            if not methods:
                methods = [match.group("method").upper()]
            if len(methods) == 1:
                status_match = re.search(r"\b(20\d|40\d|50\d)\b", header)
                blocks.append(
                    {
                        "step": int(match.group("step")),
                        "method": methods[0],
                        "expected_status": int(status_match.group(1)) if status_match else None,
                        "text": f"{header}\n{body}".strip(),
                    }
                )
                continue
            section_text = f"{header}\n{body}".strip()
            for idx, method in enumerate(methods):
                blocks.append(
                    {
                        "step": int(match.group("step")),
                        "method": method,
                        "expected_status": statuses[idx] if idx < len(statuses) else None,
                        "text": f"{section_text}\n\nCurrent HTTP subcall: {method}",
                    }
                )
        return blocks

    def _apply_block_extraction_aliases(self, step: TestStep, block_text: str) -> None:
        # The response JSONPath stays canonical, but the runtime variable gets
        # the scenario alias so later explicit mentions can resolve.
        for alias, field in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*Id)\b[^`\n]{0,80}`([A-Za-z][A-Za-z0-9_]*Id)`", block_text):
            for rule in step.extract:
                if rule.name == field:
                    step.extract.append(ExtractionRule(name=alias, expression=rule.expression, required=rule.required, description=f"Alias for {field}"))
                    return

    def _apply_block_body_overrides(self, step: TestStep, block_text: str) -> None:
        if not step.request_body:
            return
        assignments = self._field_assignments(block_text)
        for field in list(step.request_body.keys()):
            if field in assignments:
                step.request_body[field] = assignments[field]

    @staticmethod
    def _field_assignments(text: str) -> Dict[str, Any]:
        assignments: Dict[str, Any] = {}
        for field, raw_value in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*([A-Za-z][A-Za-z0-9_]*|\d+(?:[.,]\d+)?)", text):
            value: Any = raw_value.replace(",", ".")
            if re.fullmatch(r"\d+(?:\.\d+)?", value):
                value = float(value) if "." in value else int(value)
            elif field.lower().endswith("id") and raw_value.lower().endswith("id"):
                value = f"{{{{{raw_value}}}}}"
            assignments[field] = value
        return assignments

    def _endpoint_score(self, endpoint: EndpointInfo, scenario_text: str) -> int:
        text = scenario_text.lower()
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
            # Avoid selecting nested endpoints from an unrelated resource just
            # because they contain a common child identifier token.
            if len(endpoint.path.strip("/").split("/")) > 3:
                return 0
        return score

    def _endpoint_block_score(self, endpoint: EndpointInfo, block_text: str, expected_status: Optional[int]) -> int:
        score = self._endpoint_score(endpoint, block_text)
        text = block_text.lower()
        if expected_status is not None:
            statuses = {int(response.status_code) for response in endpoint.responses if response.status_code.isdigit()}
            score += 8 if expected_status in statuses else -8

        body_keys = set(endpoint.request_example.json_body.keys()) if isinstance(endpoint.request_example.json_body, dict) else set()
        for key in body_keys:
            key_l = key.lower()
            if key_l in text:
                score += 6
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
            if snake in text:
                score += 3
        if body_keys and any(marker in text for marker in ("передать", "body", "тело", "request")):
            score += 2
        if not body_keys and any(marker in text for marker in ("передать", "body", "тело")):
            score -= 6

        action = self._action_segment(endpoint.path)
        if action:
            if action.lower() in text:
                score += 6
            else:
                score -= 5
        return score

    @staticmethod
    def _action_segment(path: str) -> Optional[str]:
        segments = [segment for segment in path.strip("/").split("/") if segment and not segment.startswith("{")]
        if not segments:
            return None
        last = segments[-1]
        if re.fullmatch(r"v\d+", last, flags=re.IGNORECASE):
            return None
        # A non-resource action is usually singular and appears after a
        # path parameter, e.g. /{id}/submit or /{id}/confirm.
        raw_segments = path.strip("/").split("/")
        if len(raw_segments) >= 2 and raw_segments[-2].startswith("{") and not last.endswith("s"):
            return last
        return None

    @staticmethod
    def _text_tokens(text: str) -> set[str]:
        raw = set(re.findall(r"[A-Za-z][A-Za-z0-9_]+", text))
        tokens: set[str] = set()
        for token in raw:
            token_l = token.lower()
            tokens.add(token_l)
            tokens.update(part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token))
        return tokens

    @staticmethod
    def _primary_resource(path: str) -> Optional[str]:
        segments = [segment for segment in path.strip("/").split("/") if segment and not segment.startswith("{")]
        if segments and re.fullmatch(r"v\d+", segments[0], flags=re.IGNORECASE):
            segments = segments[1:]
        return segments[0].lower() if segments else None

    def _endpoint_tokens(self, endpoint: EndpointInfo) -> set[str]:
        raw_tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_]+", endpoint.path))
        raw_tokens.update(re.findall(r"[A-Za-z][A-Za-z0-9_]+", endpoint.operation_id or ""))
        raw_tokens.update(re.findall(r"[A-Za-z][A-Za-z0-9_]+", endpoint.summary or ""))
        raw_tokens.update(re.findall(r"[A-Za-z][A-Za-z0-9_]+", endpoint.description or ""))
        if isinstance(endpoint.request_example.json_body, dict):
            raw_tokens.update(endpoint.request_example.json_body.keys())
        for response in endpoint.responses:
            raw_tokens.update(self._json_keys(response.example))
        tokens: set[str] = set()
        for token in raw_tokens:
            tokens.add(token)
            tokens.update(part for part in re.split(r"[_\-/]", token) if part)
            tokens.update(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token))
        return tokens

    def _json_keys(self, value: Any) -> set[str]:
        keys: set[str] = set()
        if isinstance(value, dict):
            for key, item in value.items():
                keys.add(key)
                keys |= self._json_keys(item)
        elif isinstance(value, list):
            for item in value:
                keys |= self._json_keys(item)
        return keys

    def _order_by_dependencies(self, endpoints: List[EndpointInfo]) -> List[EndpointInfo]:
        def priority(endpoint: EndpointInfo) -> tuple[int, int, int, str]:
            path_params = len(re.findall(r"\{(\w+)\}", endpoint.path))
            method_rank = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 2, "DELETE": 3}.get(endpoint.method, 9)
            action_depth = max(0, len(endpoint.path.strip("/").split("/")) - path_params - 2)
            return (path_params, method_rank, action_depth, endpoint.path)

        return sorted(endpoints, key=priority)

    def _add_post_action_verifications(
        self,
        endpoints: List[EndpointInfo],
        catalog: OpenApiCatalog,
        scenario_text: str,
    ) -> List[EndpointInfo]:
        text = scenario_text.lower()
        wants_final_check = any(word in text for word in ("check", "verify", "final", "status", "провер", "статус"))
        if not wants_final_check:
            return endpoints
        result: List[EndpointInfo] = []
        for endpoint in endpoints:
            result.append(endpoint)
            if endpoint.method not in {"POST", "PUT", "PATCH", "DELETE"}:
                continue
            parent_path = "/" + "/".join(endpoint.path.strip("/").split("/")[:-1])
            verifier = catalog.find("GET", parent_path)
            if verifier is not None:
                result.append(verifier)
        return result

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
        for response in endpoint.responses:
            if response.status_code.startswith("2"):
                return int(response.status_code)
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
        assertions: List[Assertion] = []
        if isinstance(example, dict):
            for name, path in self._id_paths(example).items():
                assertions.append(Assertion(description=f"{name} is present", path=path, operator="not_null"))
                break
        elif isinstance(example, list):
            for name, path in self._id_paths(example).items():
                assertions.append(Assertion(description=f"{name} is present", path=path, operator="not_null"))
                break
        return assertions

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

    @staticmethod
    def _response_for_status(endpoint: EndpointInfo, status: int):
        for response in endpoint.responses:
            if response.status_code == str(status):
                return response
        for response in endpoint.responses:
            if response.status_code.startswith("2"):
                return response
        return endpoint.responses[0] if endpoint.responses else None

    def _normalize(
        self,
        card: ScenarioCard,
        catalog: OpenApiCatalog,
        constants: Dict[str, Any],
        constant_descriptions: Optional[Dict[str, str]] = None,
    ) -> ScenarioCard:
        constant_descriptions = constant_descriptions or {}
        card.constant_variables = {**constants, **card.constant_variables}
        card.constant_descriptions = {**constant_descriptions, **card.constant_descriptions}
        extracted: Dict[str, tuple[int, str]] = {}
        refs: Dict[str, set[str]] = {}
        for step in card.steps:
            # Paths use OpenAPI {param} convention; LLM may emit {{param}} — normalize back.
            step.path = re.sub(r"\{\{(\w+)\}\}", r"{\1}", step.path)
            endpoint = self._find_or_repair_endpoint(step, catalog)
            if endpoint and step.headers is None:
                step.headers = endpoint.request_example.headers
            if endpoint:
                step.swagger_operation_id = step.swagger_operation_id or endpoint.operation_id
                step.swagger_notes = step.swagger_notes or {
                    "summary": endpoint.summary,
                    "description": endpoint.description,
                    "comments": endpoint.request_example.comments,
                }
                self._repair_body_nested(step, endpoint)
            for param in re.findall(r"\{(\w+)\}", step.path):
                step.path_params.setdefault(param, f"{{{{{param}}}}}")
            for rule in step.extract:
                extracted.setdefault(rule.name, (step.step, rule.expression))
            refs_for_step = self._refs_by_location(step)
            for name, locations in refs_for_step.items():
                refs.setdefault(name, set()).update(locations)

        self._fix_single_brace_refs(card)
        self._auto_repair_extractions(card, catalog, refs, extracted, constants)

        card.variable_sources = {}
        for name in sorted(refs):
            if name in card.constant_variables:
                description = card.constant_descriptions.get(name) or "Loaded from constants JSON"
                card.variable_sources[name] = VariableSource(
                    name=name,
                    kind="constant",
                    description=description,
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
        """Add missing extraction rules when Agent 1 omitted them."""
        needs = {
            name for name in refs
            if name not in extracted and name not in constants and name.lower().endswith("id")
        }
        if not needs:
            return
        # Collect first usage step for each variable to anchor the repair to the right producer step.
        first_usage: Dict[str, int] = {}
        for step in card.steps:
            for name in collect_refs([step.path, step.path_params, step.query_params, step.request_body, step.headers]):
                first_usage.setdefault(name, step.step)

        for var_name in sorted(needs):
            var_lower = var_name.lower()
            first_use = first_usage.get(var_name, len(card.steps) + 1)
            # Only look at steps BEFORE first usage (producer must precede consumer).
            candidate_steps = [s for s in sorted(card.steps, key=lambda s: s.step) if s.step < first_use]
            for step in candidate_steps:
                endpoint = catalog.find(step.method, step.path)
                if endpoint is None or step.method not in ("GET", "POST", "PUT", "PATCH"):
                    continue
                for response in endpoint.responses:
                    if not response.status_code.startswith("2"):
                        continue
                    id_paths = self._id_paths(response.example or {})
                    # Exact match first; then suffix match (e.g. victimParticipantId -> participantId).
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
                            logger.info("Normalize: auto-repaired extraction %s from step %d (%s)", var_name, step.step, matched)
                        break

    def _find_or_repair_endpoint(self, step: TestStep, catalog: OpenApiCatalog) -> Optional[EndpointInfo]:
        """Return the catalog endpoint for this step, repairing the path if necessary."""
        endpoint = catalog.find(step.method, step.path)
        if endpoint is not None:
            return endpoint

        def strip_prefix(p: str) -> str:
            p = p.strip("/")
            for pfx in ("v1/", "v2/", "v3/", "api/v1/", "api/"):
                if p.startswith(pfx):
                    return p[len(pfx):]
            return p

        # Tier 2: exact match after stripping version prefixes from both sides.
        step_bare = strip_prefix(step.path)
        for candidate in catalog.endpoints:
            if candidate.method != step.method:
                continue
            if strip_prefix(candidate.path) == step_bare:
                old = step.path
                step.path = candidate.path
                logger.info("Normalize: repaired path %r -> %r in step %d", old, step.path, step.step)
                return candidate

        return None

    def _fix_single_brace_refs(self, card: ScenarioCard) -> None:
        """Convert {varname} -> {{varname}} in body/param values when it looks like a variable reference.

        LLMs sometimes emit single-brace syntax instead of the required double-brace syntax.
        Only converts whole-string values of the form exactly "{word}" to avoid corrupting
        description strings that contain curly braces for other purposes.
        """
        _single_re = re.compile(r"^\{(\w+)\}$")
        known = set(card.constant_variables) | set(card.variable_sources)

        def fix_value(v: Any) -> Any:
            if isinstance(v, str):
                m = _single_re.match(v.strip())
                if m:
                    name = m.group(1)
                    if name in known or name.lower().endswith("id"):
                        return "{{" + name + "}}"
                return v
            if isinstance(v, dict):
                return {k: fix_value(val) for k, val in v.items()}
            if isinstance(v, list):
                return [fix_value(item) for item in v]
            return v

        for step in card.steps:
            if step.request_body:
                step.request_body = fix_value(step.request_body)
            if step.path_params:
                step.path_params = {k: fix_value(v) for k, v in step.path_params.items()}
            if step.query_params:
                step.query_params = fix_value(step.query_params)
            if step.headers:
                step.headers = fix_value(step.headers)

    def _repair_body_nested(self, step: TestStep, endpoint: EndpointInfo) -> None:
        """Flatten nested objects whose keys match top-level OpenAPI example fields.

        E.g. if the LLM generated {"contact_info": {"phone": "..."}} but the spec
        example has a flat {"phone": "..."}, extract phone to the top level.
        """
        if not step.request_body or not isinstance(endpoint.request_example.json_body, dict):
            return
        openapi_fields = set(endpoint.request_example.json_body.keys())
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
                    logger.info("Normalize: extracted nested field %r.%r -> %r in step %d", field, k, k, step.step)
                del step.request_body[field]

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
                    bindings.append(
                        VariableBinding(
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
                        )
                    )
            elif isinstance(node, dict):
                for key, value in node.items():
                    visit(value, location, f"{field_path}.{key}" if field_path else str(key))
            elif isinstance(node, list):
                for idx, value in enumerate(node):
                    visit(value, location, f"{field_path}[{idx}]")

        visit(step.path, "path", "$path")
        visit(step.path_params, "path_param", "$path_params")
        visit(step.query_params, "query", "$query")
        visit(step.headers, "header", "$headers")
        visit(step.request_body, "body", "$body")
        return bindings

    @staticmethod
    def _infer_business_rules(text: str) -> List[BusinessRule]:
        rules: List[BusinessRule] = []
        if "future" in text.lower() or "будущ" in text.lower():
            rules.append(BusinessRule(id="BR-001", description="Date-like generated values must satisfy the future-date requirement."))
        return rules

    @staticmethod
    def _scenario_name(text: str) -> str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "Generated API scenario")
        return first[:120]


class DataGeneratorAgent:
    """Agent 3: generates values by reusable policies."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.llm = create_llm(config.llm_agent3 or config.llm)

    def generate(
        self,
        variable_name: str,
        context: VariableContext,
        source: Optional[VariableSource],
        business_context: str,
        previous_error: Optional[str] = None,
    ) -> GeneratedVariable:
        logger.info("Agent3: generate %s", variable_name)
        policy = policy_for(variable_name, previous_error)
        value = execute_policy(policy, context.values(), previous_error)
        return GeneratedVariable(
            name=variable_name,
            generated_value=value,
            generator_name=policy.name,
            generator_params=(source.generation_requires if source else {}) | ({"previous_error": previous_error} if previous_error else {}),
            reason=source.generation_goal if source else f"Needed for {variable_name}",
            overwrite_reason="Regenerated after failed attempt" if previous_error else None,
            generator_function=policy.generator_function,
        )


class ExecutorAgent:
    """Agent 2: executes scenario steps with bounded retries."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.llm = create_llm(config.llm_agent2 or config.llm)
        self.generator = DataGeneratorAgent(config)
        self.http = HttpTool(timeout_seconds=config.runtime.request_timeout_seconds)

    def close(self) -> None:
        self.http.close()

    def _analyze_error_with_llm(
        self,
        step: TestStep,
        response_body: Any,
        status_code: Optional[int],
        context: VariableContext,
        endpoint_info: Optional[Any] = None,
    ) -> tuple[Optional[Dict[str, Any]], bool, Optional[str]]:
        """Analyze a request error and suggest a body patch.

        Returns (patch_dict, stop_retrying, llm_reasoning):
        - patch_dict: fields to merge into request body, or None
        - stop_retrying: True when LLM determined the error cannot be fixed by body changes
        - llm_reasoning: the model's reasoning text, or None
        """
        if self.llm is None or status_code is None or status_code >= 500:
            return None, False, None
        values = context.values()
        resolved_body = resolve_templates(step.request_body, values) if step.request_body else None
        extracted_summary = {name: var.extracted_value for name, var in context.extracted.items()}
        openapi_ctx: Dict[str, Any] = {}
        if endpoint_info is not None:
            if endpoint_info.request_example and endpoint_info.request_example.json_body:
                openapi_ctx["expected_request_example"] = endpoint_info.request_example.json_body
            if endpoint_info.responses:
                openapi_ctx["response_examples"] = {
                    r.status_code: r.example for r in endpoint_info.responses if r.example
                }
        human_msg = json.dumps(
            {
                "step_name": step.name,
                "step_notes": step.notes or step.swagger_notes.get("summary", ""),
                "method": step.method,
                "path": step.path,
                "resolved_request_body": resolved_body,
                "body_was_null": step.request_body is None,
                "server_error_status": status_code,
                "server_error_response": response_body,
                "extracted_context": extracted_summary,
                "openapi_context": openapi_ctx,
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            structured = self.llm.with_structured_output(RequestBodyPatch)
            result = structured.invoke([("system", AGENT2_ERROR_ANALYSIS_PROMPT), ("human", human_msg)])
            logger.info("Agent2: error analysis reasoning: %s", result.reasoning)
            if result.fixable and result.body_patch:
                return result.body_patch, False, result.reasoning
            logger.info("Agent2: error not fixable by body patch: %s", result.reasoning)
            return None, True, result.reasoning
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent2: LLM error analysis failed: %s", exc)
        return None, False, None

    def execute(
        self,
        card: ScenarioCard,
        base_url: str,
        catalog: Optional[Any] = None,
        max_attempts: Optional[int] = None,
    ) -> ExecutionReport:
        logger.info("Agent2: execute scenario %s", card.scenario_name)
        attempts_limit = max_attempts or self.config.runtime.max_attempts_per_step
        reasoning: List[str] = []
        traces: List[ToolTrace] = []
        corrections: List[ScenarioCorrection] = []
        active_steps = [step.model_copy(deep=True) for step in card.steps]
        final_context = self._initial_context(card)
        step_reports: List[StepExecution] = []
        successful: List[RequestRecord] = []

        context = self._initial_context(card)
        reasoning.append(f"Loaded constants: {sorted(context.constants)}")
        for step in active_steps:
            report = self._execute_step(step, card, context, base_url, attempts_limit, reasoning, traces, corrections, catalog=catalog)
            step_reports.append(report)
            if report.status == "passed" and report.final_request:
                successful.append(report.final_request)
                continue
            remaining = [item for item in active_steps if item.step > step.step]
            for skipped in remaining:
                step_reports.append(StepExecution(step=skipped.step, name=skipped.name, status="skipped", error="Previous step failed"))
            break
        final_context = context

        return ExecutionReport(
            scenario_name=card.scenario_name,
            status="passed" if len(successful) == len(active_steps) else "failed",
            reasoning=reasoning,
            steps=step_reports,
            successful_requests=successful,
            variables=final_context,
            corrections=corrections,
            traces=traces,
        )

    def _initial_context(self, card: ScenarioCard) -> VariableContext:
        return VariableContext(
            constants={
                name: ConstantVariable(
                    name=name,
                    value=value,
                    description=card.constant_descriptions.get(name) or "Loaded from constants JSON",
                )
                for name, value in card.constant_variables.items()
            }
        )

    def _execute_step(
        self,
        step: TestStep,
        card: ScenarioCard,
        context: VariableContext,
        base_url: str,
        attempts_limit: int,
        reasoning: List[str],
        traces: List[ToolTrace],
        corrections: List[ScenarioCorrection],
        catalog: Optional[Any] = None,
    ) -> StepExecution:
        history: List[RequestRecord] = []
        previous_error: Optional[str] = None
        analyzed_error_keys: set = set()
        endpoint_info = catalog.find(step.method, step.path) if catalog is not None else None

        # Proactively generate body for null-body PATCH/PUT steps.
        # A PATCH/PUT with null body silently succeeds (200 OK) but does nothing,
        # causing downstream steps to fail. Fire LLM before the first attempt.
        if step.request_body is None and step.method in ("PATCH", "PUT") and endpoint_info is not None:
            synthetic_error = {"code": "BODY_REQUIRED", "message": "Specify which fields to update."}
            patch, _, llm_reasoning = self._analyze_error_with_llm(step, synthetic_error, 400, context, endpoint_info=endpoint_info)
            if patch:
                step.request_body = patch
                reasoning.append(f"Step {step.step}: proactively generated body for null-body {step.method}")
                if llm_reasoning:
                    reasoning.append(f"Step {step.step} Agent2 reasoning: {llm_reasoning}")

        for attempt in range(1, attempts_limit + 1):
            values = context.values()
            unresolved = unresolved_refs([step.path, step.path_params, step.query_params, step.request_body, step.headers], values)
            for name in sorted(unresolved):
                source = card.variable_sources.get(name)
                if source and source.kind == "extracted":
                    previous_error = f"{name} must be extracted from step {source.source_step}, but no value exists"
                    continue
                generated = self.generator.generate(name, context, source, card.business_context, previous_error)
                context.generated[name] = generated
                reasoning.append(f"Step {step.step} attempt {attempt}: generated {name}={generated.generated_value!r}")
                traces.append(ToolTrace(agent="agent3", tool="data_generator", action="generate", input={"variable": name}, output=generated.model_dump()))

            values = context.values()
            unresolved = unresolved_refs([step.path, step.path_params, step.query_params, step.request_body, step.headers], values)
            if unresolved:
                error = f"Unresolved variables: {sorted(unresolved)}"
                history.append(self._record_failed_before_http(step, attempt, error))
                previous_error = error
                continue

            path = resolve_templates(step.path, values)
            for key, value in step.path_params.items():
                path = path.replace("{" + key + "}", str(resolve_templates(value, values)))
            headers = resolve_templates(step.headers or {"Accept": "application/json"}, values)
            query = resolve_templates(step.query_params, values) if step.query_params else None
            body = resolve_templates(step.request_body, values) if step.request_body is not None else None
            url = base_url.rstrip("/") + path

            status_code, response_body, request_error = self.http.request(step.method, url, headers, query, body)
            traces.append(ToolTrace(agent="agent2", tool="http", action="request", input={"method": step.method, "url": url}, output={"status": status_code}, success=request_error is None, error=request_error))

            checks = [CheckResult(description=f"HTTP status {step.expected_status}", passed=status_code == step.expected_status, actual=status_code, expected=step.expected_status)]
            checks.extend(evaluate_assertions(step.assertions, response_body))
            extracted, extraction_errors = ({}, [])
            if all(check.passed for check in checks):
                extracted, extraction_errors = extract_jsonpath(response_body, step.extract)
                if extraction_errors:
                    repaired = self._repair_extractions(step, response_body, extraction_errors, corrections, reasoning)
                    if repaired:
                        extracted, extraction_errors = extract_jsonpath(response_body, step.extract)
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
                        reasoning.append(f"Step {step.step}: extracted {rule.name}={extracted[rule.name]!r}")

            passed = request_error is None and all(check.passed for check in checks) and not extraction_errors
            error = None if passed else "; ".join([request_error or "", *[c.error or "" for c in checks if not c.passed], *extraction_errors]).strip("; ")

            stop_retrying = False
            if not passed and status_code is not None and 400 <= status_code < 500:
                error_code = response_body.get("code", "") if isinstance(response_body, dict) else ""
                error_key = f"{status_code}:{error_code}"
                if error_key not in analyzed_error_keys:
                    analyzed_error_keys.add(error_key)
                    patch, stop_retrying, llm_reasoning = self._analyze_error_with_llm(step, response_body, status_code, context, endpoint_info=endpoint_info)
                    if llm_reasoning:
                        reasoning.append(f"Step {step.step} attempt {attempt} Agent2 reasoning: {llm_reasoning}")
                    if not stop_retrying and patch:
                        if step.request_body is None:
                            step.request_body = {}
                        for field, new_value in patch.items():
                            old_value = step.request_body.get(field)
                            step.request_body[field] = new_value
                            reasoning.append(f"Step {step.step} attempt {attempt}: LLM patched body.{field}: {old_value!r} -> {new_value!r}")
                            corrections.append(ScenarioCorrection(
                                step=step.step,
                                correction_type="request_body_patch",
                                target=field,
                                before=old_value,
                                after=new_value,
                                reason="Agent 2 LLM error analysis suggested body correction based on server error response.",
                                evidence={"server_status": status_code, "server_response": response_body},
                                confidence="medium",
                                applied=True,
                            ))

            record = RequestRecord(
                step=step.step,
                attempt=attempt,
                name=step.name,
                method=step.method,
                url=url,
                templated_path=step.path,
                templated_headers=step.headers,
                templated_query_params=step.query_params,
                templated_request_body=step.request_body,
                request_headers=headers,
                request_query_params=query,
                request_body=body,
                response_status=status_code,
                response_body=response_body,
                checks=checks,
                extract=step.extract,
                assertions=step.assertions,
                status="passed" if passed else "failed",
                error=error,
            )
            history.append(record)
            if passed:
                reasoning.append(f"Step {step.step} passed on attempt {attempt}")
                return StepExecution(step=step.step, name=step.name, status="passed", attempts=attempt, final_request=record, attempt_history=history)

            if stop_retrying:
                reasoning.append(f"Step {step.step} attempt {attempt} failed: LLM determined error is not fixable by body changes — stopping retries")
                break

            previous_error = error
            self._clear_generated_refs(step, context)
            reasoning.append(f"Step {step.step} attempt {attempt} failed: {error}; generated variables for step will be refreshed")

        return StepExecution(step=step.step, name=step.name, status="failed", attempts=attempts_limit, final_request=history[-1] if history else None, attempt_history=history, error=previous_error)

    @staticmethod
    def _record_failed_before_http(step: TestStep, attempt: int, error: str) -> RequestRecord:
        return RequestRecord(step=step.step, attempt=attempt, name=step.name, method=step.method, url="", templated_path=step.path, status="failed", error=error)

    @staticmethod
    def _clear_generated_refs(step: TestStep, context: VariableContext) -> None:
        refs = collect_refs([step.path, step.path_params, step.query_params, step.request_body, step.headers])
        for name in refs:
            context.generated.pop(name, None)

    def _repair_extractions(
        self,
        step: TestStep,
        response_body: Any,
        extraction_errors: List[str],
        corrections: List[ScenarioCorrection],
        reasoning: List[str],
    ) -> bool:
        repaired = False
        failed_names = {
            error.split(":", 1)[0].strip()
            for error in extraction_errors
            if ":" in error
        }
        for rule in step.extract:
            if rule.name not in failed_names:
                continue
            candidates = find_jsonpath_candidates(response_body, rule.name)
            if not candidates:
                continue
            before = rule.expression
            after = candidates[0]
            if before == after:
                continue
            rule.expression = after
            rule.source = "agent2"
            correction = ScenarioCorrection(
                step=step.step,
                correction_type="extraction_expression_patch",
                target=rule.name,
                before=before,
                after=after,
                reason="Original extraction JSONPath did not match response; Agent 2 selected the closest response field for this variable.",
                evidence={
                    "extraction_errors": extraction_errors,
                    "candidates": candidates[:5],
                },
                confidence="high" if after.lower().endswith("." + rule.name.lower()) else "medium",
                applied=True,
            )
            corrections.append(correction)
            reasoning.append(f"Step {step.step}: corrected extraction for {rule.name}: {before} -> {after}")
            repaired = True
        return repaired
