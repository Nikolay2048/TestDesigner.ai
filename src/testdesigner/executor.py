"""REST execution agent."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.testdesigner.data_agent import DataAgent
from src.testdesigner.models import (
    AssertionRule,
    RequestAttempt,
    ScenarioCard,
    ScenarioStep,
    StepCheck,
    StepExecution,
)
from src.testdesigner.utils import extract_jsonpath, render_templates

logger = logging.getLogger(__name__)


class ExecutorAgent:
    def __init__(self, timeout_seconds: float = 30) -> None:
        self.client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self.client.close()

    def execute_step(self, card: ScenarioCard, step: ScenarioStep, data: DataAgent, base_url: str) -> StepExecution:
        data.ensure_for_step(card, step.step, [step.path_params, step.query_params, step.headers, step.request_body])
        values = data.snapshot()
        path = step.path
        for key, value in step.path_params.items():
            path = path.replace("{" + key + "}", str(render_templates(value, values)))
        url = base_url.rstrip("/") + path
        headers = render_templates(step.headers, values)
        query = render_templates(step.query_params, values)
        body = render_templates(step.request_body, values) if step.request_body is not None else None

        attempt = self._request(step, url, headers, query, body)
        checks = self._checks(step, attempt)
        status = "passed" if all(check.passed for check in checks) else "failed"
        error = "; ".join(check.error or check.description for check in checks if not check.passed) or None

        if status == "passed":
            try:
                data.store_extractions(
                    step.step,
                    attempt.response_body,
                    [(rule.name, rule.expression, rule.required) for rule in step.extract],
                )
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                error = str(exc)
                checks.append(StepCheck(description="extract variables", passed=False, error=str(exc)))

        return StepExecution(
            step=step.step,
            name=step.name,
            status=status,
            attempts=[attempt],
            checks=checks,
            error=error,
        )

    def _request(
        self,
        step: ScenarioStep,
        url: str,
        headers: dict[str, Any],
        query: dict[str, Any],
        body: Any,
    ) -> RequestAttempt:
        logger.info("ExecutionAgent: %s %s", step.method, url)
        try:
            response = self.client.request(step.method, url, headers=headers, params=query or None, json=body)
            try:
                response_body = response.json()
            except Exception:  # noqa: BLE001
                response_body = response.text
            return RequestAttempt(
                step=step.step,
                attempt=1,
                method=step.method,
                url=url,
                headers=headers,
                query_params=query,
                body=body,
                response_status=response.status_code,
                response_body=response_body,
            )
        except httpx.RequestError as exc:
            return RequestAttempt(
                step=step.step,
                attempt=1,
                method=step.method,
                url=url,
                headers=headers,
                query_params=query,
                body=body,
                error=str(exc),
            )

    def _checks(self, step: ScenarioStep, attempt: RequestAttempt) -> list[StepCheck]:
        checks = [
            StepCheck(
                description=f"HTTP status is {step.expected_status}",
                passed=attempt.response_status == step.expected_status and attempt.error is None,
                expected=step.expected_status,
                actual=attempt.response_status,
                error=attempt.error,
            )
        ]
        if checks[0].passed:
            checks.extend(self._assertions(step.assertions, attempt.response_body))
        return checks

    @staticmethod
    def _assertions(assertions: list[AssertionRule], body: Any) -> list[StepCheck]:
        checks: list[StepCheck] = []
        for assertion in assertions:
            try:
                matches = extract_jsonpath(body, assertion.expression)
                actual = matches[0] if matches else None
                if assertion.operator == "exists":
                    passed = bool(matches)
                elif assertion.operator == "not_null":
                    passed = actual is not None
                elif assertion.operator == "eq":
                    passed = str(actual) == str(assertion.expected)
                elif assertion.operator == "contains":
                    passed = str(assertion.expected) in str(actual)
                else:
                    passed = False
                checks.append(StepCheck(
                    description=assertion.description,
                    passed=passed,
                    expected=assertion.expected,
                    actual=actual,
                    error=None if passed else f"Assertion failed: {assertion.expression}",
                ))
            except Exception as exc:  # noqa: BLE001
                checks.append(StepCheck(description=assertion.description, passed=False, error=str(exc)))
        return checks
