"""Deterministic tools used by agents."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Set

import httpx
from jsonpath_ng import parse as jsonpath_parse  # type: ignore[import]

from src.testdesigner.models import Assertion, CheckResult, ExtractionRule

logger = logging.getLogger(__name__)

VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def collect_refs(value: Any) -> Set[str]:
    refs: Set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, str):
            refs.update(VAR_RE.findall(node))
        elif isinstance(node, dict):
            for item in node.values():
                visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return refs


def resolve_templates(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        exact = VAR_RE.fullmatch(value.strip())
        if exact:
            return variables.get(exact.group(1), value)
        return VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {key: resolve_templates(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, variables) for item in value]
    return value


def unresolved_refs(value: Any, variables: Dict[str, Any]) -> Set[str]:
    return {ref for ref in collect_refs(value) if ref not in variables}


def extract_jsonpath(body: Any, rules: Iterable[ExtractionRule]) -> tuple[Dict[str, Any], list[str]]:
    extracted: Dict[str, Any] = {}
    errors: list[str] = []
    for rule in rules:
        try:
            matches = jsonpath_parse(rule.expression).find(body)
            if matches:
                extracted[rule.name] = matches[0].value
            elif rule.required:
                errors.append(f"{rule.name}: no match for {rule.expression}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rule.name}: {exc}")
    return extracted, errors


def find_jsonpath_candidates(body: Any, variable_name: str) -> List[str]:
    """Find likely JSONPath expressions for a variable in a response body.

    This is intentionally deterministic. Agent 2 uses it to repair obvious
    Agent 1 extraction mistakes such as $.id vs $.data.id or array wrappers.
    """

    candidates: List[tuple[int, str]] = []
    target = _normalize_name(variable_name)

    def visit(node: Any, path: str, depth: int) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                next_path = f"{path}.{key}"
                if not isinstance(value, (dict, list)):
                    score = _field_match_score(target, _normalize_name(key), depth)
                    if score > 0:
                        candidates.append((score, next_path))
                visit(value, next_path, depth + 1)
        elif isinstance(node, list) and node:
            visit(node[0], f"{path}[0]", depth + 1)

    visit(body, "$", 0)
    return [path for _, path in sorted(candidates, key=lambda item: (-item[0], len(item[1]), item[1]))]


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _field_match_score(target: str, field: str, depth: int) -> int:
    if field == target:
        return 100 - depth
    if target.endswith(field) or field.endswith(target):
        return 80 - depth
    if target.endswith("id") and field == "id":
        return 50 - depth
    if field.endswith("id") and target.endswith("id") and field[:-2] in target:
        return 60 - depth
    return 0


def evaluate_assertions(assertions: Iterable[Assertion], body: Any) -> list[CheckResult]:
    return [_evaluate_one(assertion, body) for assertion in assertions]


def _evaluate_one(assertion: Assertion, body: Any) -> CheckResult:
    try:
        matches = jsonpath_parse(assertion.path).find(body)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(description=assertion.description, passed=False, expected=assertion.expected, error=str(exc))
    if assertion.operator == "exists":
        return CheckResult(description=assertion.description, passed=bool(matches), actual=len(matches), expected="exists")
    if not matches:
        return CheckResult(description=assertion.description, passed=False, expected=assertion.expected, error="path not found")
    actual = matches[0].value
    expected = assertion.expected
    if assertion.operator == "not_null":
        passed = actual is not None
    elif assertion.operator == "eq":
        passed = actual == expected or str(actual) == str(expected)
    elif assertion.operator == "ne":
        passed = actual != expected and str(actual) != str(expected)
    elif assertion.operator == "contains":
        passed = expected in actual if isinstance(actual, list) else str(expected) in str(actual)
    else:
        passed = False
    return CheckResult(
        description=assertion.description,
        passed=passed,
        actual=actual,
        expected=expected,
        error=None if passed else f"expected {assertion.operator} {expected!r}, got {actual!r}",
    )


class HttpTool:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        url: str,
        headers: Dict[str, Any] | None = None,
        query_params: Dict[str, Any] | None = None,
        body: Any = None,
    ) -> tuple[int | None, Any, str | None]:
        logger.info("HttpTool: %s %s", method, url)
        try:
            response = self.client.request(
                method=method,
                url=url,
                headers={k: str(v) for k, v in (headers or {}).items()},
                params=query_params,
                json=body,
            )
            try:
                response_body = response.json()
            except Exception:  # noqa: BLE001
                response_body = response.text
            logger.info("HttpTool: response %s", response.status_code)
            return response.status_code, response_body, None
        except httpx.RequestError as exc:
            logger.error("HttpTool: %s", exc)
            return None, None, str(exc)
