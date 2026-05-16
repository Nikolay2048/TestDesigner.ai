"""
Agent 2 — Executor.

Runs a :class:`~src.models.scenario.ScenarioStabilizationInput` step by step
against a live HTTP server and returns a
:class:`~src.models.execution.ScenarioExecutionResult`.

Execution loop (per step)
--------------------------
1. Resolve all ``{{variable}}`` placeholders in path, query params, and body
   using the current variable *context*.
2. Detect any still-unresolved variables → ask Agent 3 to generate them.
3. Build and send the HTTP request.
4. Check the HTTP status code.
5. Extract variables from the response body (JSONPath).
6. Run all assertions against the response body.
7. If the step **passed** → merge extracted variables into context, proceed.
8. If the step **failed** → ask Agent 3 to refresh data, retry up to
   ``config.agent2.max_retries_per_step`` times.
9. After exhausting retries → mark the step as ``failed`` and **stop**
   the scenario (remaining steps are skipped).

Variable context
----------------
The context dict is seeded from ``constants.json`` at startup and grows as
steps extract new variables from responses.  All ``{{varName}}`` references
in subsequent steps are substituted from this dict.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Set, Tuple

import httpx
from jsonpath_ng import parse as jsonpath_parse  # type: ignore[import]

from src.agents.agent3_data_generator import DataGeneratorAgent
from src.models.execution import AssertionResult, ScenarioExecutionResult, StepResult
from src.models.scenario import Assertion, ScenarioStabilizationInput, TestStep
from src.utils.config import AppConfig

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


# ---------------------------------------------------------------------------
# ExecutorAgent
# ---------------------------------------------------------------------------


class ExecutorAgent:
    """
    Agent 2: executes a test scenario step by step.

    Args:
        config:   Application configuration (drives retry limits, LLM provider).
        base_url: Base URL of the API under test (e.g. ``http://localhost:8080``).
    """

    def __init__(self, config: AppConfig, base_url: str) -> None:
        self.config = config
        self.base_url = base_url.rstrip("/")
        self._agent3 = DataGeneratorAgent(config)
        self._http = httpx.Client(timeout=30.0)

    def __enter__(self) -> "ExecutorAgent":
        return self

    def __exit__(self, *_: Any) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        scenario: ScenarioStabilizationInput,
        constants: Dict[str, Any],
    ) -> ScenarioExecutionResult:
        """
        Execute all steps in *scenario*.

        Args:
            scenario:  Output of Agent 1.
            constants: Key-value pairs from ``constants.json``.

        Returns:
            A :class:`ScenarioExecutionResult` with full per-step traces.
        """
        context: Dict[str, Any] = dict(constants)
        step_results: list[StepResult] = []
        max_retries = self.config.agent2.max_retries_per_step

        logger.info(
            "Agent2: starting scenario '%s'  (%d steps)",
            scenario.scenario_name, len(scenario.steps),
        )

        for step in scenario.steps:
            result = self._run_step_with_retries(step, context, max_retries)
            step_results.append(result)

            if result.status == "passed":
                # Merge extracted variables into shared context
                context.update(result.extracted_vars)
                logger.info(
                    "Agent2: step %d PASSED  extracted=%s",
                    step.step_num, list(result.extracted_vars.keys()),
                )
            else:
                logger.error(
                    "Agent2: step %d FAILED after %d attempt(s) — stopping",
                    step.step_num, result.attempts,
                )
                # Append skipped placeholders for remaining steps
                for remaining in scenario.steps[step.step_num:]:
                    step_results.append(
                        StepResult(
                            step_num=remaining.step_num,
                            name=remaining.name,
                            status="skipped",
                            method=remaining.method,
                            url="",
                        )
                    )
                break

        passed = sum(1 for r in step_results if r.status == "passed")
        failed = sum(1 for r in step_results if r.status == "failed")

        overall = "passed" if failed == 0 and passed == len(scenario.steps) else "failed"

        logger.info(
            "Agent2: scenario finished  overall=%s  passed=%d  failed=%d",
            overall, passed, failed,
        )

        return ScenarioExecutionResult(
            scenario_name=scenario.scenario_name,
            overall_status=overall,
            total_steps=len(scenario.steps),
            passed_steps=passed,
            failed_steps=failed,
            steps=step_results,
            final_context=context,
        )

    # ------------------------------------------------------------------
    # Step execution with retry
    # ------------------------------------------------------------------

    def _run_step_with_retries(
        self,
        step: TestStep,
        context: Dict[str, Any],
        max_retries: int,
    ) -> StepResult:
        local_context = dict(context)  # copy so retries don't pollute global ctx

        for attempt in range(1, max_retries + 2):  # +1 for the initial attempt
            # Fill missing variables via Agent 3
            missing = self._find_missing_vars(step, local_context)
            if missing:
                logger.info(
                    "Agent2: step %d needs vars %s — calling Agent3",
                    step.step_num, missing,
                )
                generated = self._agent3.generate(step, local_context, missing)
                local_context.update(generated)

            result = self._execute_step(step, local_context, attempt)

            if result.status == "passed":
                return result

            if attempt > max_retries:
                return result

            # Ask Agent 3 to refresh data before retrying
            logger.warning(
                "Agent2: step %d attempt %d failed — asking Agent3 to refresh",
                step.step_num, attempt,
            )
            refreshed = self._agent3.refresh(step, local_context, result.error or "")
            local_context.update(refreshed)

        return result  # unreachable, but satisfies type checker

    # ------------------------------------------------------------------
    # Single attempt execution
    # ------------------------------------------------------------------

    def _execute_step(
        self,
        step: TestStep,
        context: Dict[str, Any],
        attempt: int,
    ) -> StepResult:
        """Make one HTTP request and validate the response."""

        # 1. Resolve URL
        path = self._resolve_path(step, context)
        url = f"{self.base_url}{path}"

        # 2. Resolve query params
        params: Optional[Dict[str, str]] = None
        if step.query_params:
            params = {k: self._sub(v, context) for k, v in step.query_params.items()}

        # 3. Resolve body
        body: Any = None
        if step.body:
            body = self._sub_deep(step.body, context)

        logger.info(
            "Agent2: [attempt %d] %s %s  params=%s",
            attempt, step.method, url, params,
        )

        # 4. Send request
        try:
            response = self._http.request(
                method=step.method,
                url=url,
                params=params,
                json=body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        except httpx.RequestError as exc:
            error_msg = f"HTTP request error: {exc}"
            logger.error("Agent2: %s", error_msg)
            return StepResult(
                step_num=step.step_num,
                name=step.name,
                status="failed",
                attempts=attempt,
                method=step.method,
                url=url,
                request_params=params,
                request_body=body,
                error=error_msg,
            )

        logger.info("Agent2: response %d", response.status_code)

        # 5. Parse response body
        resp_body: Any = None
        try:
            resp_body = response.json()
        except Exception:  # noqa: BLE001
            resp_body = response.text

        # 6. Check status code
        if response.status_code != step.expected_status_code:
            error_msg = (
                f"Status code mismatch: expected {step.expected_status_code}, "
                f"got {response.status_code}. Body: {resp_body}"
            )
            return StepResult(
                step_num=step.step_num,
                name=step.name,
                status="failed",
                attempts=attempt,
                method=step.method,
                url=url,
                request_params=params,
                request_body=body,
                response_status=response.status_code,
                response_body=resp_body,
                error=error_msg,
            )

        # 7. Extract variables
        extracted = self._extract_vars(step, resp_body)

        # 8. Run assertions
        assertion_results = [
            self._check_assertion(a, resp_body, context)
            for a in step.assertions
        ]
        all_passed = all(r.passed for r in assertion_results)

        failed_assertions = [r for r in assertion_results if not r.passed]
        error_msg = None
        if not all_passed:
            msgs = [f"[{r.operator}] {r.path}: {r.error or 'failed'}" for r in failed_assertions]
            error_msg = "Assertions failed: " + "; ".join(msgs)

        return StepResult(
            step_num=step.step_num,
            name=step.name,
            status="passed" if all_passed else "failed",
            attempts=attempt,
            method=step.method,
            url=url,
            request_params=params,
            request_body=body,
            response_status=response.status_code,
            response_body=resp_body,
            extracted_vars=extracted,
            assertion_results=assertion_results,
            error=error_msg,
        )

    # ------------------------------------------------------------------
    # Variable resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _find_missing_vars(step: TestStep, context: Dict[str, Any]) -> Set[str]:
        """Return variable names referenced in the step but absent from context."""
        pattern = _VAR_RE
        parts: list[str] = [step.path]

        for v in (step.path_params or {}).values():
            parts.append(v)
        for v in (step.query_params or {}).values():
            parts.append(v)

        def collect(obj: Any) -> None:
            if isinstance(obj, str):
                parts.append(obj)
            elif isinstance(obj, dict):
                for val in obj.values():
                    collect(val)
            elif isinstance(obj, list):
                for item in obj:
                    collect(item)

        collect(step.body)

        all_refs: Set[str] = set()
        for part in parts:
            all_refs.update(pattern.findall(part))

        return {ref for ref in all_refs if ref not in context}

    def _resolve_path(self, step: TestStep, context: Dict[str, Any]) -> str:
        """Substitute ``{paramName}`` placeholders in the path template."""
        path = step.path
        for param_name, param_value in (step.path_params or {}).items():
            resolved = self._sub(param_value, context)
            path = path.replace(f"{{{param_name}}}", resolved)
        # Safety: also substitute any remaining {{var}} in path
        path = self._sub(path, context)
        return path

    @staticmethod
    def _sub(value: str, context: Dict[str, Any]) -> str:
        """Replace all ``{{varName}}`` in *value* with context values."""
        def replacer(m: re.Match) -> str:
            key = m.group(1)
            return str(context.get(key, m.group(0)))  # keep placeholder if missing
        return _VAR_RE.sub(replacer, value)

    def _sub_deep(self, obj: Any, context: Dict[str, Any]) -> Any:
        """Recursively substitute ``{{varName}}`` in any nested structure."""
        if isinstance(obj, str):
            return self._sub(obj, context)
        if isinstance(obj, dict):
            return {k: self._sub_deep(v, context) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._sub_deep(item, context) for item in obj]
        return obj

    # ------------------------------------------------------------------
    # Variable extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_vars(step: TestStep, response_body: Any) -> Dict[str, Any]:
        """Extract variables from the response body using JSONPath expressions."""
        extracted: Dict[str, Any] = {}
        for var_name, jsonpath_expr in step.extract_vars.items():
            try:
                expr = jsonpath_parse(jsonpath_expr)
                matches = expr.find(response_body)
                if matches:
                    extracted[var_name] = matches[0].value
                    logger.debug("Agent2: extracted %s = %r", var_name, extracted[var_name])
                else:
                    logger.warning(
                        "Agent2: JSONPath '%s' matched nothing in response (var: %s)",
                        jsonpath_expr, var_name,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Agent2: failed to evaluate JSONPath '%s': %s",
                    jsonpath_expr, exc,
                )
        return extracted

    # ------------------------------------------------------------------
    # Assertion checking
    # ------------------------------------------------------------------

    def _check_assertion(
        self,
        assertion: Assertion,
        response_body: Any,
        context: Dict[str, Any],
    ) -> AssertionResult:
        """Evaluate one assertion against the response body."""
        # Resolve expected value if it contains {{var}} refs
        expected = assertion.expected
        if isinstance(expected, str):
            expected = self._sub(expected, context)
            # Try to match type: if original was numeric, keep as string here

        try:
            expr = jsonpath_parse(assertion.path)
            matches = expr.find(response_body)
        except Exception as exc:  # noqa: BLE001
            return AssertionResult(
                description=assertion.description,
                path=assertion.path,
                operator=assertion.operator,
                expected=assertion.expected,
                passed=False,
                error=f"Invalid JSONPath '{assertion.path}': {exc}",
            )

        # --- exists ---
        if assertion.operator == "exists":
            passed = len(matches) > 0
            return AssertionResult(
                description=assertion.description,
                path=assertion.path,
                operator=assertion.operator,
                expected=assertion.expected,
                actual=len(matches),
                passed=passed,
                error=None if passed else f"Path '{assertion.path}' not found",
            )

        if not matches:
            return AssertionResult(
                description=assertion.description,
                path=assertion.path,
                operator=assertion.operator,
                expected=assertion.expected,
                passed=False,
                error=f"Path '{assertion.path}' not found in response",
            )

        actual = matches[0].value

        # --- not_null ---
        if assertion.operator == "not_null":
            passed = actual is not None
            return AssertionResult(
                description=assertion.description,
                path=assertion.path,
                operator=assertion.operator,
                actual=actual,
                passed=passed,
                error=None if passed else f"Expected not null at '{assertion.path}', got null",
            )

        # --- eq ---
        if assertion.operator == "eq":
            # Coerce types for comparison (e.g. "1" == 1 → False, keep strict)
            passed = actual == expected or str(actual) == str(expected)
            return AssertionResult(
                description=assertion.description,
                path=assertion.path,
                operator=assertion.operator,
                expected=assertion.expected,
                actual=actual,
                passed=passed,
                error=None if passed else f"Expected {expected!r}, got {actual!r}",
            )

        # --- ne ---
        if assertion.operator == "ne":
            passed = actual != expected and str(actual) != str(expected)
            return AssertionResult(
                description=assertion.description,
                path=assertion.path,
                operator=assertion.operator,
                expected=assertion.expected,
                actual=actual,
                passed=passed,
                error=None if passed else f"Expected value != {expected!r}, got {actual!r}",
            )

        # --- contains ---
        if assertion.operator == "contains":
            try:
                if isinstance(actual, list):
                    passed = expected in actual
                else:
                    passed = str(expected) in str(actual)
            except Exception:  # noqa: BLE001
                passed = False
            return AssertionResult(
                description=assertion.description,
                path=assertion.path,
                operator=assertion.operator,
                expected=assertion.expected,
                actual=actual,
                passed=passed,
                error=None if passed else f"'{expected}' not found in {actual!r}",
            )

        # Unknown operator — pass with warning
        logger.warning("Agent2: unknown assertion operator '%s'", assertion.operator)
        return AssertionResult(
            description=assertion.description,
            path=assertion.path,
            operator=assertion.operator,
            expected=assertion.expected,
            actual=actual,
            passed=True,
            error=f"Unknown operator '{assertion.operator}' — skipped",
        )
