"""LLM-first scenario planner agent."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from src.testdesigner.llm import LlmClient, LlmError
from src.testdesigner.models import (
    ExtractionRule,
    OpenApiContract,
    ParsedScenario,
    ScenarioCard,
    ScenarioStep,
    VariableSource,
)
from src.testdesigner.utils import collect_variables, find_jsonpath_candidates

PLANNER_SYSTEM = """
You are PlannerAgent for REST API test design.

You receive:
- unstructured system-analysis scenario text;
- endpoint lines extracted from the scenario;
- relevant OpenAPI endpoints with request examples and response examples;
- constants available at runtime.

Build a ScenarioCard JSON object.

Rules:
1. Create exactly one step per endpoint line, in the same order.
2. Use only REST endpoints from endpoint_lines/OpenAPI.
3. Infer request bodies from scenario intent and OpenAPI examples.
4. Use {{variable}} templates for data generated before request or extracted from earlier responses.
5. Mark variables as:
   - constant: value comes from constants;
   - generated: data must be created before request;
   - extracted: value comes from an earlier response.
6. For each 2xx response that produces ids needed later, add extraction rules using JSONPath.
7. For negative expected statuses do not add required extraction rules.
8. Do not add semantic Postman assertions. Only status and extraction are needed.

Return ONLY JSON in this shape:
{
  "name": "...",
  "description": "...",
  "business_rules": ["..."],
  "steps": [
    {
      "step": 1,
      "name": "...",
      "method": "POST",
      "path": "/v1/...",
      "expected_status": 201,
      "headers": {"Accept":"application/json","Content-Type":"application/json"},
      "query_params": {},
      "path_params": {"id":"{{id}}"},
      "request_body": {},
      "extract": [{"name":"id","expression":"$.id","required":true}],
      "assertions": [],
      "notes": "why"
    }
  ],
  "variables": {
    "id": {"name":"id","kind":"extracted","source_step":1,"extraction":"$.id","locations":["path"]},
    "date": {"name":"date","kind":"generated","generator":"future_iso_datetime","locations":["body"]}
  }
}
"""


class PlannerAgent:
    def __init__(self, llm: LlmClient) -> None:
        if not llm.enabled:
            raise LlmError("PlannerAgent requires an enabled LLM provider")
        self.llm = llm

    def plan(self, parsed: ParsedScenario, contract: OpenApiContract, constants: dict[str, Any]) -> ScenarioCard:
        relevant = [self._endpoint_payload(contract.find(step.method, step.path)) for step in parsed.steps]
        payload = {
            "scenario_text": parsed.raw_text,
            "endpoint_lines": [step.model_dump() for step in parsed.steps],
            "constants": constants,
            "openapi_endpoints": relevant,
        }
        card_payload = self._ask_for_card(payload)
        self._apply_defaults(card_payload, parsed)
        try:
            card = ScenarioCard.model_validate(card_payload)
        except ValidationError as exc:
            retry_payload = {
                **payload,
                "previous_invalid_response": card_payload,
                "validation_error": str(exc),
                "repair_instruction": "Return a complete ScenarioCard object. Empty JSON is invalid.",
            }
            card_payload = self._ask_for_card(retry_payload)
            self._apply_defaults(card_payload, parsed)
            try:
                card = ScenarioCard.model_validate(card_payload)
            except ValidationError as retry_exc:
                raise LlmError(f"PlannerAgent returned invalid ScenarioCard after retry: {retry_exc}") from retry_exc
        quality_issues = self._quality_issues(card, parsed, contract)
        if quality_issues:
            retry_payload = {
                **payload,
                "previous_invalid_response": card.model_dump(),
                "quality_error": quality_issues,
                "repair_instruction": "Rebuild the ScenarioCard with exactly one step per endpoint_lines item. Use only those method/path values.",
            }
            card_payload = self._ask_for_card(retry_payload)
            self._apply_defaults(card_payload, parsed)
            try:
                card = ScenarioCard.model_validate(card_payload)
            except ValidationError:
                pass
            if self._quality_issues(card, parsed, contract):
                self._align_to_endpoint_lines(card, parsed, contract)
        self._normalize(card, parsed, constants, contract)
        return card

    @staticmethod
    def _apply_defaults(payload: dict[str, Any], parsed: ParsedScenario) -> None:
        payload.setdefault("name", parsed.title)
        payload.setdefault("description", "")
        payload.setdefault("business_rules", parsed.business_rules)
        payload.setdefault("variables", {})

    @staticmethod
    def _quality_issues(card: ScenarioCard, parsed: ParsedScenario, contract: OpenApiContract) -> list[str]:
        issues: list[str] = []
        if len(card.steps) != len(parsed.steps):
            issues.append(f"step_count: expected {len(parsed.steps)}, got {len(card.steps)}")
        for index, step in enumerate(card.steps[: len(parsed.steps)]):
            expected = parsed.steps[index]
            if step.method != expected.method or step.path != expected.path:
                issues.append(
                    f"step_{index + 1}_endpoint: expected {expected.method} {expected.path}, got {step.method} {step.path}"
                )
            if contract.find(step.method, step.path) is None:
                issues.append(f"step_{index + 1}_unknown_endpoint: {step.method} {step.path}")
        return issues

    @staticmethod
    def _align_to_endpoint_lines(card: ScenarioCard, parsed: ParsedScenario, contract: OpenApiContract) -> None:
        aligned: list[ScenarioStep] = []
        for index, parsed_step in enumerate(parsed.steps, start=1):
            current = card.steps[index - 1] if index - 1 < len(card.steps) else None
            endpoint = contract.find(parsed_step.method, parsed_step.path)
            name = current.name if current and current.name else PlannerAgent._step_name(parsed_step.text, parsed_step.method, parsed_step.path)
            body = current.request_body if current and current.request_body is not None else parsed_step.request_body
            if body is None and endpoint is not None:
                body = endpoint.request_example
            query_params = current.query_params if current and current.query_params else parsed_step.query_params
            path_params = current.path_params if current and current.path_params else {}
            for param_name in re.findall(r"\{(\w+)\}", parsed_step.path):
                path_params.setdefault(param_name, f"{{{{{param_name}}}}}")
            expected_status = parsed_step.expected_status or (current.expected_status if current else None)
            if expected_status is None:
                expected_status = PlannerAgent._default_status(endpoint)
            aligned.append(ScenarioStep(
                step=index,
                name=name,
                method=parsed_step.method,
                path=parsed_step.path,
                expected_status=expected_status,
                headers=current.headers if current else {},
                query_params=query_params,
                path_params=path_params,
                request_body=body,
                extract=current.extract if current else [],
                assertions=[],
                notes=current.notes if current else "",
            ))
        card.steps = aligned

    @staticmethod
    def _step_name(text: str, method: str, path: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.lower().startswith("endpoint:"):
                return line
        return f"{method} {path}"

    @staticmethod
    def _default_status(endpoint: Any) -> int:
        if endpoint is None:
            return 200
        statuses = [int(response.status_code) for response in endpoint.responses if response.status_code.isdigit()]
        for status in statuses:
            if 200 <= status < 300:
                return status
        return statuses[0] if statuses else 200

    @staticmethod
    def _negative_status(text: str, endpoint: Any) -> int | None:
        if endpoint is None:
            return None
        available = {int(response.status_code) for response in endpoint.responses if response.status_code.isdigit()}
        text = re.split(r"\n\s*Шаг\s+\d+", text, maxsplit=1)[0]
        lowered = text.lower()
        if any(token in lowered for token in ("дубл", "повтор", "уже существует", "already")) and 409 in available:
            return 409
        if any(token in lowered for token in ("несуществ", "не найден", "not found")) and 404 in available:
            return 404
        if any(token in lowered for token in ("заблок", "blocked")) and 403 in available:
            return 403
        if any(token in lowered for token in ("валидац", "прошл", "past", "missing", "required", "без суммы")) and 400 in available:
            return 400
        if any(token in lowered for token in ("ошиб", "отклон", "запрещ")):
            for status in (409, 400, 403):
                if status in available:
                    return status
        return None

    def _ask_for_card(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = self.llm.json(PLANNER_SYSTEM, payload)
        return self._coerce_card_payload(raw)

    @staticmethod
    def _coerce_card_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        for key in ("card", "scenario_card", "scenarioCard"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                payload = nested
                break
        if "name" not in payload:
            scenarios = payload.get("scenarios")
            if isinstance(scenarios, list) and scenarios and isinstance(scenarios[0], dict):
                payload = scenarios[0]
            elif isinstance(payload.get("scenario"), dict):
                payload = payload["scenario"]
        PlannerAgent._coerce_steps(payload)
        variables = payload.get("variables")
        if isinstance(variables, list):
            mapped: dict[str, Any] = {}
            for item in variables:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("key")
                if not name:
                    continue
                normalized = dict(item)
                if "type" in normalized and "kind" not in normalized:
                    normalized["kind"] = normalized.pop("type")
                mapped[name] = normalized
            payload["variables"] = mapped
        for item in (payload.get("variables") or {}).values():
            if not isinstance(item, dict):
                continue
            if "type" in item and "kind" not in item:
                item["kind"] = item.pop("type")
            if item.get("kind") not in {"constant", "generated", "extracted", "literal"}:
                if item.get("source_step") or item.get("extraction"):
                    item["kind"] = "extracted"
                elif "value" in item:
                    item["kind"] = "literal"
                else:
                    item["kind"] = "generated"
        return payload

    @staticmethod
    def _coerce_steps(payload: dict[str, Any]) -> None:
        steps = payload.get("steps")
        if not isinstance(steps, list):
            return
        for index, item in enumerate(steps, start=1):
            if not isinstance(item, dict):
                continue
            endpoint = item.get("endpoint")
            if isinstance(endpoint, str):
                match = re.match(r"\s*(GET|POST|PUT|PATCH|DELETE)\s+(\S+)\s*", endpoint, flags=re.IGNORECASE)
                if match:
                    item.setdefault("method", match.group(1).upper())
                    item.setdefault("path", match.group(2))
            raw_step = item.get("step")
            if not isinstance(raw_step, int):
                if isinstance(raw_step, str) and raw_step.startswith("/"):
                    item.setdefault("path", raw_step)
                item["step"] = index
            if "path" not in item and isinstance(item.get("url"), str):
                item["path"] = item["url"]
            if "request_body" not in item and "body" in item:
                item["request_body"] = item["body"]
            if not item.get("name"):
                item["name"] = item.get("description") or item.get("notes") or f"{item.get('method', 'GET')} {item.get('path', '')}".strip()
            item.setdefault("headers", {})
            item.setdefault("query_params", {})
            item.setdefault("path_params", {})
            item.setdefault("extract", [])
            item.setdefault("assertions", [])

    def _normalize(
        self,
        card: ScenarioCard,
        parsed: ParsedScenario,
        constants: dict[str, Any],
        contract: OpenApiContract,
    ) -> None:
        for step in card.steps:
            step.path = re.sub(r"\{\{(\w+)\}\}", r"{\1}", step.path)
            parsed_step = parsed.steps[step.step - 1] if 0 <= step.step - 1 < len(parsed.steps) else None
            endpoint = contract.find(step.method, step.path)
            negative_status = self._negative_status(parsed_step.text if parsed_step else "", endpoint)
            if negative_status and step.expected_status < 400:
                step.expected_status = negative_status
            for name in re.findall(r"\{(\w+)\}", step.path):
                step.path_params.setdefault(name, f"{{{{{name}}}}}")
            if step.headers is None:
                step.headers = {}
            step.headers.setdefault("Accept", "application/json")
            if step.request_body is not None:
                step.headers.setdefault("Content-Type", "application/json")
            if step.expected_status >= 400:
                for rule in step.extract:
                    rule.required = False
        refs = self._refs(card)
        self._ensure_missing_extractions(card, contract, refs, constants)
        self._disambiguate_repeated_ids(card, parsed)
        refs = self._refs(card)
        extractions = {rule.name: (step.step, rule.expression) for step in card.steps for rule in step.extract}
        for name, locations in refs.items():
            if name in card.variables:
                source = card.variables[name]
                source.locations = sorted(set(source.locations or locations))
                if source.kind != "extracted" and name in extractions:
                    source_step, expression = extractions[name]
                    source.kind = "extracted"
                    source.source_step = source_step
                    source.extraction = expression
                    source.value = None
                    source.generator = None
                continue
            if name in constants:
                card.variables[name] = VariableSource(name=name, kind="constant", value=constants[name], locations=locations)
            elif name in extractions:
                source_step, expression = extractions[name]
                card.variables[name] = VariableSource(
                    name=name,
                    kind="extracted",
                    source_step=source_step,
                    extraction=expression,
                    locations=locations,
                )
            else:
                card.variables[name] = VariableSource(name=name, kind="generated", generator="auto", locations=locations)

    @staticmethod
    def _ensure_missing_extractions(
        card: ScenarioCard,
        contract: OpenApiContract,
        refs: dict[str, list[str]],
        constants: dict[str, Any],
    ) -> None:
        extracted = {rule.name for step in card.steps for rule in step.extract}
        for step_index, consumer in enumerate(card.steps):
            for name in collect_variables(consumer.path_params):
                if name in constants or name in extracted:
                    continue
                for producer in reversed(card.steps[:step_index]):
                    if producer.expected_status >= 400:
                        continue
                    expression = PlannerAgent._response_expression(contract, producer, name)
                    if not expression:
                        continue
                    producer.extract.append(ExtractionRule(name=name, expression=expression, required=True))
                    extracted.add(name)
                    refs.setdefault(name, [])
                    if "path" not in refs[name]:
                        refs[name].append("path")
                    break

    @staticmethod
    def _response_expression(contract: OpenApiContract, step: ScenarioStep, name: str) -> str | None:
        endpoint = contract.find(step.method, step.path)
        if endpoint:
            for response in endpoint.responses:
                if not response.status_code.startswith("2") or response.example is None:
                    continue
                candidates = find_jsonpath_candidates(response.example, name)
                if candidates:
                    return candidates[0]
        if name.lower().endswith("id"):
            return f"$.{name}"
        return None

    @staticmethod
    def _disambiguate_repeated_ids(card: ScenarioCard, parsed: ParsedScenario) -> None:
        PlannerAgent._alias_repeated_id(
            card,
            parsed,
            create_path="/v1/accidents/{accidentId}/claims",
            base_name="claimId",
        )
        PlannerAgent._alias_repeated_id(
            card,
            parsed,
            create_path="/v1/accidents/{accidentId}/claims/{claimId}/assessments",
            base_name="assessmentId",
        )
        PlannerAgent._alias_repeated_id(
            card,
            parsed,
            create_path="/v1/accidents/{accidentId}/claims/{claimId}/payments",
            base_name="paymentId",
        )

    @staticmethod
    def _alias_repeated_id(card: ScenarioCard, parsed: ParsedScenario, create_path: str, base_name: str) -> None:
        all_producers = [step for step in card.steps if step.method == "POST" and step.path == create_path]
        for step in all_producers:
            if step.expected_status >= 400:
                step.extract = [
                    rule for rule in step.extract
                    if rule.name != base_name and not re.fullmatch(rf"{re.escape(base_name)}\d+", rule.name)
                ]
        producers = [step for step in all_producers if step.expected_status < 400]
        if len(producers) < 2:
            return
        aliases: dict[int, str] = {}
        for occurrence, step in enumerate(producers, start=1):
            alias = f"{base_name}{occurrence}"
            aliases[occurrence] = alias
            step.extract = [
                rule for rule in step.extract
                if rule.name != base_name and not re.fullmatch(rf"{re.escape(base_name)}\d+", rule.name)
            ]
            if not any(rule.name == alias for rule in step.extract):
                step.extract.append(ExtractionRule(name=alias, expression=f"$.{base_name}", required=True))
        for step in card.steps:
            if f"{{{base_name}}}" not in step.path:
                continue
            ordinal = PlannerAgent._ordinal_near(parsed, step.step)
            if ordinal in aliases:
                step.path_params[base_name] = f"{{{{{aliases[ordinal]}}}}}"

    @staticmethod
    def _ordinal_near(parsed: ParsedScenario, step_number: int) -> int | None:
        indexes = [step_number - 1, step_number, step_number - 2]
        for index in indexes:
            if 0 <= index < len(parsed.steps):
                ordinal = PlannerAgent._ordinal(parsed.steps[index].text)
                if ordinal:
                    return ordinal
        return None

    @staticmethod
    def _ordinal(text: str) -> int | None:
        lowered = text.lower()
        candidates: list[tuple[int, int]] = []
        for ordinal, tokens in (
            (1, ("перв", "first")),
            (2, ("втор", "second")),
            (3, ("трет", "third")),
        ):
            positions = [lowered.find(token) for token in tokens if token in lowered]
            if positions:
                candidates.append((min(positions), ordinal))
        return min(candidates)[1] if candidates else None

    @staticmethod
    def _refs(card: ScenarioCard) -> dict[str, list[str]]:
        refs: dict[str, list[str]] = {}
        for step in card.steps:
            for location, payload in (
                ("path", step.path_params),
                ("query", step.query_params),
                ("header", step.headers),
                ("body", step.request_body),
            ):
                for name in collect_variables(payload):
                    refs.setdefault(name, [])
                    if location not in refs[name]:
                        refs[name].append(location)
        return refs

    @staticmethod
    def _endpoint_payload(endpoint: Any) -> dict[str, Any]:
        if endpoint is None:
            return {}
        return {
            "method": endpoint.method,
            "path": endpoint.path,
            "summary": endpoint.summary,
            "description": endpoint.description,
            "parameters": [param.model_dump(by_alias=True) for param in endpoint.parameters],
            "request_example": endpoint.request_example,
            "responses": [
                {
                    "status_code": response.status_code,
                    "description": response.description,
                    "example": response.example,
                }
                for response in endpoint.responses
            ],
        }
