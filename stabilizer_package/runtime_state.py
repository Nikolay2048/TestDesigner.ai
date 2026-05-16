from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from .models import (
    ExecutedStep,
    ExecutionContext,
    GeneratedVariable,
    ScenarioVariable,
    StabilizedRequest,
    StabilizedScenarioResult,
)


class RuntimeState(BaseModel):
    """Authoritative runtime state. LLM can inspect it through tools, but Python owns it."""

    scenario_name: str
    execution_context: ExecutionContext = Field(default_factory=ExecutionContext)
    executed_steps: Dict[int, ExecutedStep] = Field(default_factory=dict)
    stabilized_requests: List[StabilizedRequest] = Field(default_factory=list)
    proposals: List[str] = Field(default_factory=list)

    def snapshot_context(self) -> Dict[str, Any]:
        return self.execution_context.model_dump()

    def get_response_json(self, step: int) -> Any:
        if step not in self.executed_steps:
            raise KeyError(f"No executed response found for step {step}")
        return self.executed_steps[step].result.response_json

    def build_result(self) -> StabilizedScenarioResult:
        variables: Dict[str, ScenarioVariable] = {}

        for name, variable in self.execution_context.variables.items():
            variables[name] = ScenarioVariable(
                name=name,
                value=variable.extracted_value,
                source_type="extracted",
                source_step=variable.source_step,
                extraction_expression=variable.extraction_expression,
                used_in_steps=[],
            )

        for name, variable in self.execution_context.generated_variables.items():
            variables[name] = ScenarioVariable(
                name=name,
                value=variable.generated_value,
                source_type="generated",
                generator_name=variable.generator_name,
                generator_params=variable.generator_params,
                used_in_steps=[],
            )

        # Detect usage in templated request bodies by string search.
        for request in self.stabilized_requests:
            templated = str(request.templated_request_body or {}) + str(request.headers or {}) + str(request.query_params or {})
            for variable_name in variables:
                if f"{{{{ {variable_name} }}}}" in templated or f"{{{{{variable_name}}}}}" in templated:
                    variables[variable_name].used_in_steps.append(request.step)

        return StabilizedScenarioResult(
            scenario_name=self.scenario_name,
            requests=self.stabilized_requests,
            variables=list(variables.values()),
            execution_context=self.execution_context,
            proposals=self.proposals,
        )
