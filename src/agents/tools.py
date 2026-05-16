"""Tool layer used by the multi-agent runtime.

The agents communicate through structured models, but Agent 2 performs concrete
actions through explicit tools. This keeps HTTP execution, variable resolution,
JSONPath extraction, and validation auditable and replaceable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, Optional, Set

import httpx
from jsonpath_ng import parse as jsonpath_parse  # type: ignore[import]

from src.models.execution import (
    AssertionResult,
    ConstantVariable,
    ExtractedVariable,
    ToolCallRecord,
    VariableContext,
)
from src.models.scenario import Assertion, ExtractionRule, TestStep

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class VariableContextTool:
    """Stores and reads typed runtime variables."""

    name = "variable_context"

    def seed_constants(self, constants: Dict[str, Any]) -> tuple[VariableContext, ToolCallRecord]:
        context = VariableContext()
        for name, value in constants.items():
            context.constants[name] = ConstantVariable(
                name=name,
                value=value,
                description=f"Loaded from constants JSON as {name}.",
            )
        record = ToolCallRecord(
            agent="agent2",
            tool=self.name,
            action="seed_constants",
            input_summary={"count": len(constants)},
            output_summary={"keys": sorted(constants.keys())},
        )
        logger.info("Tool variable_context: seeded %d constants", len(constants))
        return context, record

    def save_extracted(
        self,
        context: VariableContext,
        name: str,
        value: Any,
        source_step: int,
        expression: str,
    ) -> ToolCallRecord:
        overwrite_reason = None
        if name in context.extracted:
            overwrite_reason = f"Re-extracted on step {source_step}."
        context.extracted[name] = ExtractedVariable(
            name=name,
            extracted_value=value,
            source_step=source_step,
            extraction_expression=expression,
            overwrite_reason=overwrite_reason,
        )
        logger.info("Tool variable_context: saved extracted %s=%r", name, value)
        return ToolCallRecord(
            agent="agent2",
            tool=self.name,
            action="save_extracted",
            input_summary={"name": name, "source_step": source_step, "expression": expression},
            output_summary={"value": value},
        )


class TemplateResolverTool:
    """Finds and resolves `{{variable}}` references in request templates."""

    name = "template_resolver"

    def missing_variables(self, step: TestStep, context: VariableContext) -> Set[str]:
        values = context.values()
        refs = collect_template_refs(
            [
                step.path,
                step.path_params,
                step.headers,
                step.query_params,
                step.request_body,
            ]
        )
        return {ref for ref in refs if ref not in values}

    def resolve_step(
        self,
        step: TestStep,
        base_url: str,
        context: VariableContext,
    ) -> tuple[Dict[str, Any], ToolCallRecord]:
        values = context.values()
        path = resolve_templates(step.path, values)
        for param, param_value in (step.path_params or {}).items():
            path = path.replace(f"{{{param}}}", resolve_templates(param_value, values))

        headers = resolve_templates(step.headers or {}, values)
        query = resolve_templates(step.query_params or {}, values)
        body = resolve_templates(step.request_body, values) if step.request_body is not None else None
        url = f"{base_url.rstrip('/')}{path}"
        record = ToolCallRecord(
            agent="agent2",
            tool=self.name,
            action="resolve_step",
            input_summary={"step": step.step, "refs": sorted(collect_template_refs([step.path, step.query_params, step.request_body]))},
            output_summary={"url": url, "has_body": body is not None},
        )
        return {"url": url, "headers": headers, "query": query or None, "body": body}, record


class RestRequestTool:
    """Executes HTTP requests."""

    name = "rest_request"

    def __init__(self, timeout: float = 30.0) -> None:
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, Any]],
        query: Optional[Dict[str, Any]],
        body: Optional[Any],
    ) -> tuple[Optional[int], Any, ToolCallRecord]:
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        try:
            response = self._client.request(
                method=method,
                url=url,
                headers={k: str(v) for k, v in request_headers.items()},
                params=query,
                json=body,
            )
            try:
                response_body = response.json()
            except Exception:  # noqa: BLE001
                response_body = response.text
            logger.info("Tool rest_request: %s %s -> %s", method, url, response.status_code)
            return (
                response.status_code,
                response_body,
                ToolCallRecord(
                    agent="agent2",
                    tool=self.name,
                    action="request",
                    input_summary={"method": method, "url": url},
                    output_summary={"status": response.status_code},
                ),
            )
        except httpx.RequestError as exc:
            logger.error("Tool rest_request: request failed: %s", exc)
            return (
                None,
                None,
                ToolCallRecord(
                    agent="agent2",
                    tool=self.name,
                    action="request",
                    input_summary={"method": method, "url": url},
                    success=False,
                    error=str(exc),
                ),
            )


class JsonPathExtractionTool:
    """Extracts variables from response bodies."""

    name = "jsonpath_extractor"

    def extract(self, body: Any, rules: Iterable[ExtractionRule], step: int) -> tuple[Dict[str, Any], ToolCallRecord]:
        extracted: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        for rule in rules:
            try:
                expr = jsonpath_parse(rule.expression)
                matches = expr.find(body)
                if matches:
                    extracted[rule.name] = matches[0].value
                elif rule.required:
                    errors[rule.name] = f"No match for {rule.expression}"
            except Exception as exc:  # noqa: BLE001
                errors[rule.name] = str(exc)
        success = not errors
        logger.info("Tool jsonpath_extractor: step %s extracted=%s errors=%s", step, list(extracted), errors)
        return extracted, ToolCallRecord(
            agent="agent2",
            tool=self.name,
            action="extract",
            input_summary={"step": step, "rules": [r.model_dump() for r in rules]},
            output_summary={"extracted": extracted, "errors": errors},
            success=success,
            error=json.dumps(errors, ensure_ascii=False) if errors else None,
        )


class StatusCodeValidatorTool:
    """Checks expected HTTP status."""

    name = "status_validator"

    def validate(self, expected: int, actual: Optional[int]) -> tuple[AssertionResult, ToolCallRecord]:
        passed = actual == expected
        result = AssertionResult(
            description=f"HTTP status is {expected}",
            operator="eq",
            expected=expected,
            actual=actual,
            passed=passed,
            error=None if passed else f"Expected HTTP {expected}, got {actual}",
        )
        return result, ToolCallRecord(
            agent="agent2",
            tool=self.name,
            action="validate",
            input_summary={"expected": expected, "actual": actual},
            output_summary={"passed": passed},
            success=passed,
            error=result.error,
        )


class BusinessCheckTool:
    """Evaluates machine assertions. Text criteria are recorded as placeholders."""

    name = "business_check"

    def check(self, assertions: Iterable[Assertion], body: Any) -> tuple[list[AssertionResult], ToolCallRecord]:
        results = [self._check_one(assertion, body) for assertion in assertions]
        passed = all(result.passed for result in results)
        return results, ToolCallRecord(
            agent="agent2",
            tool=self.name,
            action="check",
            input_summary={"assertions": [a.model_dump() for a in assertions]},
            output_summary={"passed": passed, "count": len(results)},
            success=passed,
            error=None if passed else "One or more business checks failed",
        )

    def _check_one(self, assertion: Assertion, body: Any) -> AssertionResult:
        try:
            matches = jsonpath_parse(assertion.path).find(body)
        except Exception as exc:  # noqa: BLE001
            return AssertionResult(
                description=assertion.description,
                path=assertion.path,
                operator=assertion.operator,
                expected=assertion.expected,
                passed=False,
                error=str(exc),
            )

        if assertion.operator == "exists":
            passed = bool(matches)
            return self._result(assertion, len(matches), passed, "Path not found")
        if not matches:
            return self._result(assertion, None, False, "Path not found")

        actual = matches[0].value
        expected = assertion.expected
        if assertion.operator == "not_null":
            return self._result(assertion, actual, actual is not None, "Expected non-null value")
        if assertion.operator == "eq":
            return self._result(assertion, actual, actual == expected or str(actual) == str(expected), f"Expected {expected!r}, got {actual!r}")
        if assertion.operator == "ne":
            return self._result(assertion, actual, actual != expected and str(actual) != str(expected), f"Expected value different from {expected!r}")
        if assertion.operator == "contains":
            passed = expected in actual if isinstance(actual, list) else str(expected) in str(actual)
            return self._result(assertion, actual, passed, f"Expected {actual!r} to contain {expected!r}")
        return self._result(assertion, actual, True, None)

    @staticmethod
    def _result(assertion: Assertion, actual: Any, passed: bool, error: Optional[str]) -> AssertionResult:
        return AssertionResult(
            description=assertion.description,
            path=assertion.path,
            operator=assertion.operator,
            expected=assertion.expected,
            actual=actual,
            passed=passed,
            error=None if passed else error,
        )


def collect_template_refs(values: Iterable[Any]) -> Set[str]:
    refs: Set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            refs.update(_VAR_RE.findall(value))
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for value in values:
        visit(value)
    return refs


def resolve_templates(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        full_match = _VAR_RE.fullmatch(value.strip())
        if full_match:
            return variables.get(full_match.group(1), value)

        def replace(match: re.Match[str]) -> str:
            return str(variables.get(match.group(1), match.group(0)))

        return _VAR_RE.sub(replace, value)
    if isinstance(value, dict):
        return {key: resolve_templates(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, variables) for item in value]
    return value
