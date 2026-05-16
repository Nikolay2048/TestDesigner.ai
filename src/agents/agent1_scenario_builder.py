"""Agent 1: scenario builder.

Agent 1 reads a system-analysis scenario and the parsed OpenAPI catalog, then
produces an executable scenario card for Agent 2. It uses structured LLM output
for semantic mapping and deterministic post-processing for path normalization,
variable classification, extraction rules, and basic assertions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable

from langchain_core.messages import HumanMessage, SystemMessage

from src.models.scenario import (
    Assertion,
    BusinessRule,
    ExtractionRule,
    ScenarioStabilizationInput,
    TestStep,
    VariableSource,
)
from src.modules.swagger_parser import EndpointDescriptor, ParsedSpec
from src.utils.config import AppConfig
from src.utils.llm_factory import create_llm

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


_SYSTEM_PROMPT = """\
You are Agent 1 in a multi-agent API testing system.

Read a Russian system-analysis scenario and a resolved OpenAPI catalog. Return
only a ScenarioStabilizationInput object.

Rules:
- Map only API calls that are present in the OpenAPI catalog.
- Preserve business intent in business_context and business_rules.
- Use constants from CONSTANT_VARIABLES as {{name}} templates when appropriate.
- Use {{variableName}} templates for values produced by previous responses or by Agent 3.
- Keep endpoint path templates exactly as OpenAPI declares them, e.g. /v1/bookings/{bookingId}.
- Put concrete path values into path_params, e.g. {"bookingId": "{{bookingId}}"}.
- Put query parameters into query_params; never append ?x=y to path.
- Add extract_variables for ids and useful fields needed by later steps.
- Add assertions for explicit business outcomes when they can be expressed by JSONPath.
- For generated variables, add generation_goal and generation_requires.
- expected_status is the expected HTTP status, including negative-test statuses.
"""


class ScenarioBuilderAgent:
    """Builds an executable scenario card from text plus parsed OpenAPI."""

    name = "agent1"

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._llm = create_llm(config)

    def build(
        self,
        scenario_text: str,
        spec: ParsedSpec,
        constants: Dict[str, Any],
    ) -> ScenarioStabilizationInput:
        logger.info("Agent1: building scenario card endpoints=%d", len(spec.endpoints))
        llm = self._llm.with_structured_output(ScenarioStabilizationInput)
        result: ScenarioStabilizationInput = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=self._human_message(scenario_text, spec, constants)),
            ]
        )
        result = self._normalize(result, spec, constants)
        logger.info(
            "Agent1: scenario=%s steps=%d variables=%d rules=%d",
            result.scenario_name,
            len(result.steps),
            len(result.variable_sources),
            len(result.business_rules),
        )
        return result

    def _human_message(self, scenario_text: str, spec: ParsedSpec, constants: Dict[str, Any]) -> str:
        return "\n\n".join(
            [
                "# CONSTANT_VARIABLES",
                json.dumps(constants, ensure_ascii=False, indent=2),
                "# OPENAPI_CATALOG",
                spec.operation_catalog(),
                "# SCENARIO_TEXT",
                scenario_text,
            ]
        )

    def _normalize(
        self,
        scenario: ScenarioStabilizationInput,
        spec: ParsedSpec,
        constants: Dict[str, Any],
    ) -> ScenarioStabilizationInput:
        endpoints_by_key = {(ep.method, ep.path): ep for ep in spec.endpoints}
        spec_paths = [ep.path for ep in spec.endpoints]
        scenario.constant_variables = {**constants, **scenario.constant_variables}
        if scenario.description and not scenario.business_context:
            scenario.business_context = scenario.description

        for index, step in enumerate(scenario.steps, start=1):
            step.step = step.step or index
            step.method = step.method.upper()  # type: ignore[assignment]
            step.path = self._normalize_path(step.path, spec_paths)
            self._move_query_string(step)
            self._fill_path_params(step)

            endpoint = endpoints_by_key.get((step.method, step.path))
            if endpoint:
                step.endpoint_operation_id = endpoint.operation_id
                self._seed_defaults_from_endpoint(step, endpoint, constants)
                self._ensure_extracts_from_response(step, endpoint)
                self._ensure_assertions_from_criteria(step, endpoint)
            else:
                logger.warning("Agent1: no OpenAPI endpoint for %s %s", step.method, step.path)

        scenario.variable_sources = self._classify_variables(scenario, constants)
        for step in scenario.steps:
            step.variable_sources = [source for source in scenario.variable_sources if source.name in self._refs_in_step(step)]
        return scenario

    @staticmethod
    def _move_query_string(step: TestStep) -> None:
        if "?" not in step.path:
            return
        path, raw_query = step.path.split("?", 1)
        step.path = path
        query = dict(step.query_params or {})
        for pair in raw_query.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                query.setdefault(key, value)
        step.query_params = query

    @staticmethod
    def _fill_path_params(step: TestStep) -> None:
        for name in _PATH_PARAM_RE.findall(step.path):
            step.path_params.setdefault(name, f"{{{{{name}}}}}")

    def _normalize_path(self, path: str, spec_paths: list[str]) -> str:
        path = re.sub(r"\{\{\s*(\w+)\s*\}\}", r"{\1}", path.split("?", 1)[0])
        if path in spec_paths:
            return path
        concrete_regexes = [(self._path_regex(spec_path), spec_path) for spec_path in spec_paths]
        for regex, spec_path in concrete_regexes:
            if regex.match(path):
                return spec_path
        return path

    @staticmethod
    def _path_regex(path: str) -> re.Pattern[str]:
        pattern = re.escape(path)
        pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", pattern)
        return re.compile(f"^{pattern}$")

    def _seed_defaults_from_endpoint(
        self,
        step: TestStep,
        endpoint: EndpointDescriptor,
        constants: Dict[str, Any],
    ) -> None:
        headers = dict(step.headers or {})
        for key, value in endpoint.example_request.headers.items():
            headers.setdefault(key, value)
        step.headers = headers or None

        query = dict(step.query_params or {})
        for param in endpoint.parameters:
            if param.location == "query":
                query.setdefault(param.name, self._constant_or_example(param.name, param.example, constants))
        step.query_params = query or None

        if step.request_body is None and endpoint.request_body and isinstance(endpoint.request_body.example, dict):
            step.request_body = self._template_request_body(endpoint.request_body.example, constants)

    @staticmethod
    def _constant_or_example(name: str, example: Any, constants: Dict[str, Any]) -> Any:
        if name in constants:
            return f"{{{{{name}}}}}"
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        if snake in constants:
            return f"{{{{{snake}}}}}"
        return example

    def _template_request_body(self, body: Dict[str, Any], constants: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in body.items():
            result[key] = self._constant_or_example(key, value, constants)
            if result[key] == value and self._looks_dynamic(key, value):
                result[key] = f"{{{{{key}}}}}"
        return result

    @staticmethod
    def _looks_dynamic(key: str, value: Any) -> bool:
        lowered = key.lower()
        return lowered.endswith("id") or "date" in lowered or value in (None, "string")

    def _ensure_extracts_from_response(self, step: TestStep, endpoint: EndpointDescriptor) -> None:
        known = {rule.name for rule in step.extract_variables}
        success_response = self._success_response(endpoint, step.expected_status)
        example = success_response.example if success_response else None
        for name, path in self._candidate_extracts(example).items():
            if name not in known and self._is_referenced_later_name(name, step):
                step.extract_variables.append(ExtractionRule(name=name, expression=path))
                known.add(name)
        for param_value in step.path_params.values():
            for ref in _VAR_RE.findall(str(param_value)):
                if ref not in known and example and ref in self._flatten_keys(example):
                    step.extract_variables.append(ExtractionRule(name=ref, expression=f"$.{ref}", required=False))

    @staticmethod
    def _success_response(endpoint: EndpointDescriptor, expected_status: int):
        exact = [resp for resp in endpoint.responses if resp.status_code == str(expected_status)]
        if exact:
            return exact[0]
        for resp in endpoint.responses:
            if resp.status_code.startswith("2"):
                return resp
        return None

    def _candidate_extracts(self, example: Any) -> Dict[str, str]:
        found: Dict[str, str] = {}

        def walk(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    child_path = f"{path}.{key}" if path else f"$.{key}"
                    if key.lower().endswith("id"):
                        found.setdefault(key, child_path)
                    walk(item, child_path)
            elif isinstance(value, list) and value:
                walk(value[0], f"{path}[0]" if path else "$[0]")

        walk(example, "")
        return found

    @staticmethod
    def _flatten_keys(value: Any) -> set[str]:
        keys: set[str] = set()
        if isinstance(value, dict):
            for key, item in value.items():
                keys.add(key)
                keys |= ScenarioBuilderAgent._flatten_keys(item)
        elif isinstance(value, list):
            for item in value:
                keys |= ScenarioBuilderAgent._flatten_keys(item)
        return keys

    @staticmethod
    def _is_referenced_later_name(name: str, step: TestStep) -> bool:
        return name.lower().endswith("id") or name.lower() in {"status", "code"}

    def _ensure_assertions_from_criteria(self, step: TestStep, endpoint: EndpointDescriptor) -> None:
        if step.assertions:
            return
        response = self._success_response(endpoint, step.expected_status)
        example = response.example if response else None
        if not isinstance(example, dict):
            return
        criteria = " ".join(step.success_criteria).upper()
        if "CANCELLED" in criteria and "status" in example:
            step.assertions.append(Assertion(description="Status is CANCELLED", path="$.status", operator="eq", expected="CANCELLED"))
        elif "CREATED" in criteria and "status" in example:
            step.assertions.append(Assertion(description="Status is CREATED", path="$.status", operator="eq", expected="CREATED"))
        elif "REGISTERED" in criteria and "status" in example:
            step.assertions.append(Assertion(description="Status is REGISTERED", path="$.status", operator="eq", expected="REGISTERED"))
        for key in example:
            if key.lower().endswith("id"):
                step.assertions.append(Assertion(description=f"{key} is present", path=f"$.{key}", operator="not_null"))
                break

    def _classify_variables(self, scenario: ScenarioStabilizationInput, constants: Dict[str, Any]) -> list[VariableSource]:
        refs = sorted(set().union(*(self._refs_in_step(step) for step in scenario.steps)))
        extracted: Dict[str, ExtractionRule] = {}
        extracted_step: Dict[str, int] = {}
        for step in scenario.steps:
            for rule in step.extract_variables:
                extracted[rule.name] = rule
                extracted_step[rule.name] = step.step

        result: list[VariableSource] = []
        for name in refs:
            if name in constants or name in scenario.constant_variables:
                result.append(VariableSource(name=name, kind="constant", description="Loaded from constants JSON."))
            elif name in extracted:
                rule = extracted[name]
                result.append(
                    VariableSource(
                        name=name,
                        kind="extracted",
                        source_step=extracted_step[name],
                        extraction_expression=rule.expression,
                        description=rule.description,
                    )
                )
            else:
                result.append(
                    VariableSource(
                        name=name,
                        kind="generated",
                        generation_goal=f"Generate runtime value for {name}.",
                        generation_requires=self._infer_generation_requires(name, scenario.business_rules),
                    )
                )
        return result

    @staticmethod
    def _infer_generation_requires(name: str, rules: Iterable[BusinessRule]) -> Dict[str, Any]:
        lowered = name.lower()
        requires: Dict[str, Any] = {}
        if "date" in lowered:
            requires["format"] = "ISO-8601 date-time"
            requires["must_be_future"] = True
        matched_rules = [rule.description for rule in rules if name in rule.applies_to_variables]
        if matched_rules:
            requires["business_rules"] = matched_rules
        return requires

    @staticmethod
    def _refs_in_step(step: TestStep) -> set[str]:
        values = [step.path, step.path_params, step.headers, step.query_params, step.request_body]
        refs: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, str):
                refs.update(_VAR_RE.findall(value))
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for value in values:
            walk(value)
        return refs
