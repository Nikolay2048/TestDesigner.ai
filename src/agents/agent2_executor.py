"""Agent 2: scenario execution agent.

Agent 2 is the orchestration agent. It owns the variable context, calls tools,
asks Agent 3 for generated values, retries failed steps with fresh generated
data, extracts response variables, and emits an auditable execution result.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.agents.agent3_data_generator import DataGeneratorAgent
from src.agents.tools import (
    BusinessCheckTool,
    JsonPathExtractionTool,
    RestRequestTool,
    StatusCodeValidatorTool,
    TemplateResolverTool,
    VariableContextTool,
)
from src.models.execution import (
    ExecutedRequest,
    ScenarioExecutionResult,
    StepResult,
    ToolCallRecord,
    VariableContext,
)
from src.models.scenario import ScenarioStabilizationInput, TestStep, VariableSource
from src.utils.config import AppConfig

logger = logging.getLogger(__name__)


class ExecutorAgent:
    """Executes a scenario step-by-step with bounded retries."""

    name = "agent2"

    def __init__(self, config: AppConfig, base_url: str) -> None:
        self.config = config
        self.base_url = base_url.rstrip("/")
        self.agent3 = DataGeneratorAgent(config)
        self.context_tool = VariableContextTool()
        self.resolver_tool = TemplateResolverTool()
        self.request_tool = RestRequestTool()
        self.extractor_tool = JsonPathExtractionTool()
        self.status_tool = StatusCodeValidatorTool()
        self.business_tool = BusinessCheckTool()

    def __enter__(self) -> "ExecutorAgent":
        return self

    def __exit__(self, *_: Any) -> None:
        self.request_tool.close()

    def run(self, scenario: ScenarioStabilizationInput, constants: Dict[str, Any]) -> ScenarioExecutionResult:
        logger.info("Agent2: start scenario=%s steps=%d", scenario.scenario_name, len(scenario.steps))
        context, seed_call = self.context_tool.seed_constants({**constants, **scenario.constant_variables})
        tool_calls: list[ToolCallRecord] = [seed_call]
        reasoning: list[str] = [f"Loaded {len(context.constants)} constant variables."]
        step_results: list[StepResult] = []
        ready_requests: list[ExecutedRequest] = []
        max_retries = self.config.agent2.max_retries_per_step

        source_map = {source.name: source for source in scenario.variable_sources}

        for step in scenario.steps:
            result = self._run_step(
                scenario=scenario,
                step=step,
                context=context,
                source_map=source_map,
                max_retries=max_retries,
                reasoning=reasoning,
                global_tool_calls=tool_calls,
            )
            step_results.append(result)

            if result.status == "passed":
                if result.attempts_trace:
                    ready_requests.append(result.attempts_trace[-1])
                continue

            reasoning.append(f"Stopped on step {step.step}: {result.error or 'step failed'}.")
            for remaining in scenario.steps[step.step:]:
                step_results.append(
                    StepResult(
                        step_num=remaining.step,
                        name=remaining.name,
                        status="skipped",
                        method=remaining.method,
                    )
                )
            break

        passed = sum(1 for step in step_results if step.status == "passed")
        failed = sum(1 for step in step_results if step.status == "failed")
        overall = "passed" if failed == 0 and passed == len(scenario.steps) else "failed"
        logger.info("Agent2: finished overall=%s passed=%d failed=%d", overall, passed, failed)
        return ScenarioExecutionResult(
            scenario_name=scenario.scenario_name,
            overall_status=overall,
            reasoning_log=reasoning,
            total_steps=len(scenario.steps),
            passed_steps=passed,
            failed_steps=failed,
            steps=step_results,
            ready_requests=ready_requests,
            variables=context,
            final_context=context.values(),
            tool_calls=tool_calls,
        )

    def _run_step(
        self,
        scenario: ScenarioStabilizationInput,
        step: TestStep,
        context: VariableContext,
        source_map: Dict[str, VariableSource],
        max_retries: int,
        reasoning: list[str],
        global_tool_calls: list[ToolCallRecord],
    ) -> StepResult:
        logger.info("Agent2: step %d/%d %s", step.step, len(scenario.steps), step.name)
        attempts_trace: list[ExecutedRequest] = []
        last_error: Optional[str] = None

        for attempt in range(1, max_retries + 2):
            previous_error = last_error
            current_error: Optional[str] = None
            attempt_calls: list[ToolCallRecord] = []
            missing = self.resolver_tool.missing_variables(step, context)
            if missing:
                reasoning.append(f"Step {step.step} attempt {attempt}: missing variables {sorted(missing)}.")
            for var_name in sorted(missing):
                source = source_map.get(var_name)
                if source and source.kind == "extracted":
                    current_error = f"Variable {var_name} must be extracted from step {source.source_step}, but it is absent."
                    record = ToolCallRecord(
                        agent=self.name,
                        tool="variable_context",
                        action="missing_extracted_variable",
                        input_summary={"variable": var_name, "step": step.step},
                        success=False,
                        error=current_error,
                    )
                    attempt_calls.append(record)
                    global_tool_calls.append(record)
                    continue

                generated, record = self.agent3.generate_variable(
                    variable_name=var_name,
                    step=step,
                    context=context,
                    business_context=scenario.business_context,
                    business_rules=scenario.business_rules,
                    source=source,
                    previous_error=previous_error if attempt > 1 else None,
                )
                attempt_calls.append(record)
                global_tool_calls.append(record)
                if generated is not None:
                    context.generated[var_name] = generated
                    reasoning.append(
                        f"Step {step.step} attempt {attempt}: generated {var_name} using {generated.generator_name}."
                    )

            unresolved = self.resolver_tool.missing_variables(step, context)
            if unresolved:
                current_error = f"Unresolved variables remain: {sorted(unresolved)}"
                failed_attempt = self._failed_attempt(step, attempt, current_error, attempt_calls)
                attempts_trace.append(failed_attempt)
                last_error = current_error
                if attempt > max_retries:
                    return self._step_failed(step, attempt, attempts_trace, current_error)
                continue

            resolved, record = self.resolver_tool.resolve_step(step, self.base_url, context)
            attempt_calls.append(record)
            global_tool_calls.append(record)

            response_status, response_body, record = self.request_tool.request(
                method=step.method,
                url=resolved["url"],
                headers=resolved["headers"],
                query=resolved["query"],
                body=resolved["body"],
            )
            attempt_calls.append(record)
            global_tool_calls.append(record)

            status_result, record = self.status_tool.validate(step.expected_status, response_status)
            attempt_calls.append(record)
            global_tool_calls.append(record)

            business_results, record = self.business_tool.check(step.assertions, response_body)
            attempt_calls.append(record)
            global_tool_calls.append(record)

            extracted_values: Dict[str, Any] = {}
            if status_result.passed and all(result.passed for result in business_results):
                extracted_values, record = self.extractor_tool.extract(response_body, step.extract_variables, step.step)
                attempt_calls.append(record)
                global_tool_calls.append(record)
                if not record.success:
                    current_error = record.error or "Required extraction failed."

            passed = status_result.passed and all(result.passed for result in business_results) and current_error is None
            attempt_result = ExecutedRequest(
                step=step.step,
                attempt=attempt,
                name=step.name,
                method=step.method,
                url=resolved["url"],
                templated_path=step.path,
                templated_headers=step.headers,
                templated_query_params=step.query_params,
                templated_request_body=step.request_body,
                request_headers=resolved["headers"],
                request_params=resolved["query"],
                request_body=resolved["body"],
                response_status=response_status,
                response_body=response_body,
                business_check_results=[status_result, *business_results],
                tool_calls=attempt_calls,
                status="passed" if passed else "failed",
                error=None if passed else self._attempt_error(status_result, business_results, current_error),
            )
            attempts_trace.append(attempt_result)

            if passed:
                saved: Dict[str, Any] = {}
                for rule in step.extract_variables:
                    if rule.name in extracted_values:
                        save_record = self.context_tool.save_extracted(
                            context=context,
                            name=rule.name,
                            value=extracted_values[rule.name],
                            source_step=step.step,
                            expression=rule.expression,
                        )
                        global_tool_calls.append(save_record)
                        attempt_result.tool_calls.append(save_record)
                        saved[rule.name] = extracted_values[rule.name]
                reasoning.append(f"Step {step.step} passed on attempt {attempt}. Extracted: {sorted(saved)}.")
                return StepResult(
                    step_num=step.step,
                    name=step.name,
                    status="passed",
                    attempts=attempt,
                    method=step.method,
                    url=resolved["url"],
                    request_params=resolved["query"],
                    request_body=resolved["body"],
                    response_status=response_status,
                    response_body=response_body,
                    extracted_vars=saved,
                    assertion_results=[status_result, *business_results],
                    attempts_trace=attempts_trace,
                )

            last_error = attempt_result.error
            reasoning.append(f"Step {step.step} attempt {attempt} failed: {last_error}")
            if attempt > max_retries:
                return self._step_failed(step, attempt, attempts_trace, last_error)

            self._drop_generated_for_step(step, context)
            reasoning.append(f"Step {step.step}: regenerated dynamic values before retry.")

        return self._step_failed(step, max_retries + 1, attempts_trace, last_error or "Unknown error")

    @staticmethod
    def _attempt_error(
        status_result,
        business_results,
        previous_error: Optional[str],
    ) -> str:
        errors = []
        if not status_result.passed:
            errors.append(status_result.error or "status check failed")
        for result in business_results:
            if not result.passed:
                errors.append(result.error or result.description)
        if previous_error:
            errors.append(previous_error)
        return "; ".join(errors) or "Attempt failed"

    @staticmethod
    def _failed_attempt(step: TestStep, attempt: int, error: str, calls: list[ToolCallRecord]) -> ExecutedRequest:
        return ExecutedRequest(
            step=step.step,
            attempt=attempt,
            name=step.name,
            method=step.method,
            url="",
            templated_path=step.path,
            templated_headers=step.headers,
            templated_query_params=step.query_params,
            templated_request_body=step.request_body,
            tool_calls=calls,
            status="failed",
            error=error,
        )

    @staticmethod
    def _step_failed(step: TestStep, attempts: int, trace: list[ExecutedRequest], error: str) -> StepResult:
        last = trace[-1] if trace else None
        return StepResult(
            step_num=step.step,
            name=step.name,
            status="failed",
            attempts=attempts,
            method=step.method,
            url=last.url if last else "",
            request_params=last.request_params if last else None,
            request_body=last.request_body if last else None,
            response_status=last.response_status if last else None,
            response_body=last.response_body if last else None,
            assertion_results=last.business_check_results if last else [],
            error=error,
            attempts_trace=trace,
        )

    @staticmethod
    def _drop_generated_for_step(step: TestStep, context: VariableContext) -> None:
        refs = set()

        def visit(value: Any) -> None:
            import re

            if isinstance(value, str):
                refs.update(re.findall(r"\{\{\s*(\w+)\s*\}\}", value))
            elif isinstance(value, dict):
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(step.path)
        visit(step.path_params)
        visit(step.headers)
        visit(step.query_params)
        visit(step.request_body)
        for name in refs:
            context.generated.pop(name, None)
