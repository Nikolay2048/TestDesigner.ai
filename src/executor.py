from __future__ import annotations

import copy
import re
from typing import Any

import httpx

from domain import (
    DataBindingPlan,
    ExecutorStepTrace,
    ExecutorTrace,
    RequestValueBinding,
    ResolvedBindingTrace,
)
from generators import GeneratorRegistry


class FlowExecutor:
    """Executes a data binding plan against a REST API and records a detailed trace."""

    def __init__(
        self,
        base_url: str,
        static_test_data: dict[str, Any],
        generator_registry: GeneratorRegistry | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.static_test_data = static_test_data
        self.generator_registry = generator_registry or GeneratorRegistry()
        self.timeout = timeout

    def execute(self, plan: DataBindingPlan, attempt: int) -> ExecutorTrace:
        variables: dict[str, Any] = {}
        trace = ExecutorTrace(attempt=attempt, base_url=self.base_url)

        for index, step in enumerate(plan.steps, start=1):
            step_id = f"s{index:02d}"
            step_trace = ExecutorStepTrace(
                step_id=step_id,
                business_step=step.business_step,
                operation=step.operation,
                resolved_path=step.operation.path,
            )

            body: dict[str, Any] = {}
            query: dict[str, Any] = {}
            headers: dict[str, str] = {}

            try:
                for binding in step.request_bindings:
                    value, binding_trace = self._resolve_binding(binding, variables)
                    step_trace.resolved_bindings.append(binding_trace)
                    if binding_trace.error:
                        raise ValueError(binding_trace.error)
                    if binding.location == "path":
                        param_name = _path_param_name(binding.target)
                        step_trace.resolved_path = step_trace.resolved_path.replace(
                            "{" + param_name + "}",
                            str(value),
                        )
                    elif binding.location == "query":
                        query[_field_name(binding.target)] = value
                    elif binding.location == "header":
                        headers[_field_name(binding.target)] = str(value)
                    elif binding.location == "body":
                        _set_json_path(body, binding.target, value)

                step_trace.request = {
                    "method": step.operation.method,
                    "path": step_trace.resolved_path,
                    "query": query,
                    "headers": headers,
                    "body": body,
                }
                response = httpx.request(
                    step.operation.method,
                    self.base_url + step_trace.resolved_path,
                    params=query or None,
                    headers=headers or None,
                    json=body or None,
                    timeout=self.timeout,
                )
                step_trace.response_status = response.status_code
                step_trace.response_body = _response_body(response)

                if not 200 <= response.status_code < 300:
                    step_trace.status = "failed"
                    step_trace.failure = f"Expected 2xx, got {response.status_code}"
                    trace.steps.append(step_trace)
                    trace.status = "failed"
                    trace.failed_step_id = step_id
                    trace.failure = step_trace.failure
                    trace.variables = variables
                    return trace

                for extraction in step.response_extractions:
                    value = _extract_json_path(step_trace.response_body, extraction.json_path)
                    variables[extraction.variable] = value
                    step_trace.extracted_variables[extraction.variable] = value

                step_trace.status = "passed"
                trace.steps.append(step_trace)

            except Exception as exc:
                step_trace.status = "failed"
                step_trace.failure = str(exc)
                trace.steps.append(step_trace)
                trace.status = "failed"
                trace.failed_step_id = step_id
                trace.failure = str(exc)
                trace.variables = variables
                return trace

        trace.status = "passed"
        trace.variables = variables
        return trace

    def _resolve_binding(
        self,
        binding: RequestValueBinding,
        variables: dict[str, Any],
    ) -> tuple[Any, ResolvedBindingTrace]:
        binding_trace = ResolvedBindingTrace(
            target=binding.target,
            source=binding.source,
            variable=binding.variable,
            static_key=binding.static_key,
            generator=binding.generator,
            params=binding.params,
            expression=binding.expression,
            policy=binding.policy,
        )

        try:
            if binding.source == "static":
                if not binding.static_key or binding.static_key not in self.static_test_data:
                    raise KeyError(f"Unknown static key: {binding.static_key}")
                value = self.static_test_data[binding.static_key]
            elif binding.source == "generated":
                if not binding.generator:
                    raise ValueError(f"Generated binding has no generator: {binding.target}")
                value = self.generator_registry.generate(
                    binding.generator,
                    _resolve_template_values(binding.params, self.static_test_data, variables),
                )
            elif binding.source == "response":
                if not binding.variable or binding.variable not in variables:
                    raise KeyError(f"Unknown response variable: {binding.variable}")
                value = variables[binding.variable]
            elif binding.source == "computed":
                if not binding.expression:
                    raise ValueError(f"Computed binding has no expression: {binding.target}")
                value = _evaluate_expression(binding.expression, variables, self.static_test_data)
            elif binding.source == "literal":
                value = binding.literal
            else:
                raise ValueError(f"Unknown binding source for {binding.target}: {binding.source}")
        except Exception as exc:
            binding_trace.error = str(exc)
            return None, binding_trace

        binding_trace.value_preview = _preview(value)
        return value, binding_trace


def _response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _path_param_name(target: str) -> str:
    return target.removeprefix("$.path.")


def _field_name(target: str) -> str:
    return target.split(".")[-1]


def _set_json_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in path.removeprefix("$.").split(".") if part]
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    if parts:
        current[parts[-1]] = value


def _extract_json_path(value: Any, path: str) -> Any:
    current = value
    for part in _json_path_parts(path):
        if part == "[]":
            if not isinstance(current, list) or not current:
                raise KeyError(f"Cannot extract from empty array at {path}")
            current = current[0]
            continue
        if isinstance(part, int):
            current = current[part]
        else:
            current = current[part]
    return current


def _json_path_parts(path: str) -> list[str | int]:
    raw_parts = path.removeprefix("$.").split(".")
    parts: list[str | int] = []
    for raw in raw_parts:
        if raw.endswith("[]"):
            parts.append(raw[:-2])
            parts.append("[]")
            continue
        match = re.fullmatch(r"(.+)\[(\d+)]", raw)
        if match:
            parts.append(match.group(1))
            parts.append(int(match.group(2)))
        elif raw:
            parts.append(raw)
    return parts


def _resolve_template_values(
    params: dict[str, Any],
    static_test_data: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    resolved = copy.deepcopy(params)
    for key, value in list(resolved.items()):
        if isinstance(value, str):
            match = re.fullmatch(r"{{\s*([A-Za-z0-9_]+)\s*}}", value)
            if match:
                name = match.group(1)
                resolved[key] = variables.get(name, static_test_data.get(name, value))
    return resolved


def _evaluate_expression(expression: str, variables: dict[str, Any], static_test_data: dict[str, Any]) -> Any:
    rendered = expression
    values = {**static_test_data, **variables}
    for name, value in values.items():
        rendered = rendered.replace("{{" + name + "}}", repr(value))
    if not re.fullmatch(r"[0-9\s+\-*/().']+", rendered):
        raise ValueError(f"Unsafe or unsupported expression: {expression}")
    return eval(rendered, {"__builtins__": {}}, {})


def _preview(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 80:
        return value[:77] + "..."
    return value
