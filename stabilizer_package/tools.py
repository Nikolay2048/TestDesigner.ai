from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urljoin

import requests
from langchain_core.tools import StructuredTool

from .data_generation_agent import DataGenerationAgent
from .models import (
    ExecutedRequest,
    ExecutedStep,
    ExtractedVariable,
    GeneratedVariable,
    RequestExecutionResult,
    StabilizedRequest,
)
from .runtime_state import RuntimeState


def _extract_value(data: Any, expression: str) -> Any:
    expression = expression.strip()
    if expression.startswith("$."):
        expression = expression[2:]
    elif expression.startswith("response."):
        expression = expression.replace("response.", "", 1)

    current = data
    for part in expression.split("."):
        array_match = re.fullmatch(r"(\w+)\[(\d+)]", part)
        if array_match:
            key = array_match.group(1)
            index = int(array_match.group(2))
            current = current[key][index]
        else:
            current = current[part]
    return current


def _get_context_value(state: RuntimeState, variable_name: str) -> Any:
    context = state.execution_context
    if variable_name in context.variables:
        return context.variables[variable_name].extracted_value
    if variable_name in context.generated_variables:
        return context.generated_variables[variable_name].generated_value
    raise KeyError(f"Variable '{variable_name}' not found in execution context")


def _resolve_templates(data: Any, state: RuntimeState) -> Any:
    pattern = r"\{\{\s*(\w+)\s*\}\}"

    def resolve(value: Any) -> Any:
        if isinstance(value, str):
            matches = list(re.finditer(pattern, value))
            if not matches:
                return value
            if len(matches) == 1 and matches[0].group(0) == value:
                return _get_context_value(state, matches[0].group(1))
            return re.sub(pattern, lambda m: str(_get_context_value(state, m.group(1))), value)
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return resolve(data)


def create_scenario_tools(state: RuntimeState, data_generation_agent: DataGenerationAgent) -> List[StructuredTool]:
    """Create stateful tools. The LLM cannot pass fake responses because tools read RuntimeState."""

    def get_context_variables() -> Dict[str, Any]:
        """Return current execution context with all extracted and generated variables. Read-only."""
        return state.execution_context.model_dump()

    def get_previous_responses() -> Dict[str, Any]:
        """Return real responses already received from API, grouped by step number. Read-only source of truth."""
        return {
            str(step): executed.model_dump()
            for step, executed in state.executed_steps.items()
        }

    def execute_rest_request(
        step: int,
        name: str,
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
        base_url: str,
        path: str,
        headers: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        request_body: Optional[Dict[str, Any]] = None,
        expected_status: Optional[int] = None,
        timeout_seconds: int = 15,
    ) -> Dict[str, Any]:
        """Execute API request and store real request/response in runtime state.

        Pass scenario step number and original step fields.
        This tool automatically:
        - builds absolute URL from base_url + path;
        - resolves {{ variables }} from RuntimeState in path, headers, query_params, and request_body;
        - sends HTTP request;
        - stores actual request and actual response by step number.

        Do not manually replace templates before calling this tool.
        Do not pass response_json or status_code to this tool.
        """
        try:
            resolved_path = _resolve_templates(path, state)
            resolved_headers = _resolve_templates(headers, state)
            resolved_query_params = _resolve_templates(query_params, state)
            resolved_request_body = _resolve_templates(request_body, state)
            url = urljoin(base_url.rstrip("/") + "/", str(resolved_path).lstrip("/"))
        except Exception as exc:
            result = RequestExecutionResult(error=f"Template resolution failed: {exc}")
            return result.model_dump()

        request_snapshot = ExecutedRequest(
            step=step,
            name=name,
            method=method,
            url=url,
            path=path,
            headers=resolved_headers,
            query_params=resolved_query_params,
            request_body=resolved_request_body,
            templated_request_body=request_body,
        )

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=resolved_headers,
                params=resolved_query_params,
                json=resolved_request_body,
                timeout=timeout_seconds,
            )
            try:
                response_json = response.json()
                response_text = None
            except ValueError:
                response_json = None
                response_text = response.text

            result = RequestExecutionResult(
                status_code=response.status_code,
                response_json=response_json,
                response_text=response_text,
                response_headers=dict(response.headers),
                error=None,
            )
        except requests.RequestException as exc:
            result = RequestExecutionResult(error=str(exc))

        state.executed_steps[step] = ExecutedStep(step=step, request=request_snapshot, result=result)

        # Store/update request chain for Postman export. Assertion pass is filled later by compare/validation if needed.
        if expected_status is not None:
            passed = result.status_code == expected_status
            state.stabilized_requests = [r for r in state.stabilized_requests if r.step != step]
            state.stabilized_requests.append(
                StabilizedRequest(
                    step=step,
                    name=name,
                    method=method,
                    path=path,
                    headers=headers,
                    query_params=query_params,
                    resolved_request_body=resolved_request_body,
                    templated_request_body=request_body,
                    expected_status=expected_status,
                    actual_status=result.status_code,
                    passed=passed,
                )
            )

        return {
            "request": request_snapshot.model_dump(),
            "result": result.model_dump(),
        }

    def compare_status_code(source_step: int, expected_status: int) -> Dict[str, Any]:
        """Compare expected status code with actual status code from real stored response for source_step."""
        if source_step not in state.executed_steps:
            return {"passed": False, "error": f"Step {source_step} has no stored response"}
        actual = state.executed_steps[source_step].result.status_code
        passed = actual == expected_status

        for request in state.stabilized_requests:
            if request.step == source_step:
                request.actual_status = actual
                request.expected_status = expected_status
                request.passed = passed

        return {
            "passed": passed,
            "expected_status": expected_status,
            "actual_status": actual,
            "message": "Status code matched." if passed else f"Expected {expected_status}, got {actual}.",
        }

    def validate_assertions(source_step: int, assertions: List[str]) -> Dict[str, Any]:
        """Validate simple assertions against real stored response_json for source_step.

        Supports ==, !=, >, >=, <, <= with left side as response path.
        Unsupported assertions are returned as skipped.
        """
        if source_step not in state.executed_steps:
            return {"passed": False, "error": f"Step {source_step} has no stored response"}

        response_json = state.executed_steps[source_step].result.response_json
        checked, failed, skipped = [], [], []
        operators = ["==", "!=", ">=", "<=", ">", "<"]

        def parse_literal(raw: str) -> Any:
            raw = raw.strip()
            if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
                return raw[1:-1]
            if raw.isdigit():
                return int(raw)
            try:
                return float(raw)
            except ValueError:
                return raw

        for assertion in assertions:
            operator = next((op for op in operators if op in assertion), None)
            if operator is None:
                skipped.append({"assertion": assertion, "reason": "Unsupported assertion format"})
                continue
            left_raw, right_raw = assertion.split(operator, 1)
            try:
                left = _extract_value(response_json, left_raw.strip())
                right = parse_literal(right_raw)
                passed = {
                    "==": left == right,
                    "!=": left != right,
                    ">": left > right,
                    ">=": left >= right,
                    "<": left < right,
                    "<=": left <= right,
                }[operator]
                item = {"assertion": assertion, "passed": passed, "actual": left, "expected": right, "operator": operator}
                checked.append(item)
                if not passed:
                    failed.append(item)
            except Exception as exc:
                failed.append({"assertion": assertion, "passed": False, "error": str(exc)})

        passed_all = len(failed) == 0
        for request in state.stabilized_requests:
            if request.step == source_step:
                request.passed = request.passed and passed_all

        return {"passed": passed_all, "checked": checked, "failed": failed, "skipped": skipped}

    def extract_response_value(source_step: int, extraction_expression: str) -> Dict[str, Any]:
        """Extract value from real stored response_json of source_step. Does not save it automatically."""
        if source_step not in state.executed_steps:
            return {"success": False, "error": f"Step {source_step} has no stored response"}
        try:
            value = _extract_value(state.executed_steps[source_step].result.response_json, extraction_expression)
            return {"success": True, "value": value, "source_step": source_step, "extraction_expression": extraction_expression}
        except Exception as exc:
            return {"success": False, "error": str(exc), "source_step": source_step, "extraction_expression": extraction_expression}

    def save_extracted_variable_to_context(
        variable_name: str,
        extracted_value: Any,
        source_step: int,
        extraction_expression: str,
        overwrite: bool = False,
        overwrite_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save extracted variable into runtime context. Use overwrite=True only when previous value was wrong."""
        if variable_name in state.execution_context.variables and not overwrite:
            return {"success": False, "error": f"Variable {variable_name} already exists. Use overwrite=True with reason."}
        state.execution_context.variables[variable_name] = ExtractedVariable(
            extracted_value=extracted_value,
            source_step=source_step,
            extraction_expression=extraction_expression,
            overwrite_reason=overwrite_reason,
        )
        return {"success": True, "context": state.execution_context.model_dump()}

    def ask_data_generation_agent(
        field_name: str,
        field_schema: Dict[str, Any],
        business_context: str,
        generation_goal: str,
    ) -> Dict[str, Any]:
        """Ask Data Generation Agent to generate independent test data. Does not save generated value automatically."""
        return data_generation_agent.generate(field_name, field_schema, business_context, generation_goal).model_dump()

    def save_generated_variable_to_context(
        variable_name: str,
        generated_value: Any,
        generator_name: str,
        generator_params: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        overwrite: bool = False,
        overwrite_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save generated variable into runtime context. Use overwrite=True only when previous value was wrong."""
        if variable_name in state.execution_context.generated_variables and not overwrite:
            return {"success": False, "error": f"Variable {variable_name} already exists. Use overwrite=True with reason."}
        state.execution_context.generated_variables[variable_name] = GeneratedVariable(
            generated_value=generated_value,
            generator_name=generator_name,
            generator_params=generator_params or {},
            reason=reason,
            overwrite_reason=overwrite_reason,
        )
        return {"success": True, "context": state.execution_context.model_dump()}

    def build_stabilized_scenario_result() -> Dict[str, Any]:
        """Build final structured artifact for Postman collection generation."""
        return state.build_result().model_dump()

    return [
        StructuredTool.from_function(get_context_variables),
        StructuredTool.from_function(get_previous_responses),
        StructuredTool.from_function(execute_rest_request),
        StructuredTool.from_function(compare_status_code),
        StructuredTool.from_function(validate_assertions),
        StructuredTool.from_function(extract_response_value),
        StructuredTool.from_function(save_extracted_variable_to_context),
        StructuredTool.from_function(ask_data_generation_agent),
        StructuredTool.from_function(save_generated_variable_to_context),
        StructuredTool.from_function(build_stabilized_scenario_result),
    ]
