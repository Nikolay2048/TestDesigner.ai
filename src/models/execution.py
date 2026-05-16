"""Execution and variable-context contracts for Agent 2."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class ConstantVariable(BaseModel):
    """Reference value loaded from human-maintained JSON."""

    type: Literal["constant"] = "constant"
    name: str
    value: Any
    description: str = ""


class GeneratedVariable(BaseModel):
    """Variable generated independently from previous responses."""

    type: Literal["generated"] = "generated"
    name: str
    generated_value: Any
    generator_name: str
    generator_params: Dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    overwrite_reason: Optional[str] = None
    generator_function: Optional[str] = None


class ExtractedVariable(BaseModel):
    """Variable extracted from a previous API response."""

    type: Literal["extracted"] = "extracted"
    name: str
    extracted_value: Any
    source_step: int
    extraction_expression: str
    overwrite_reason: Optional[str] = None


class VariableContext(BaseModel):
    """Typed variable context accumulated during execution."""

    constants: Dict[str, ConstantVariable] = Field(default_factory=dict)
    generated: Dict[str, GeneratedVariable] = Field(default_factory=dict)
    extracted: Dict[str, ExtractedVariable] = Field(default_factory=dict)

    def values(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        data.update({name: var.value for name, var in self.constants.items()})
        data.update({name: var.generated_value for name, var in self.generated.items()})
        data.update({name: var.extracted_value for name, var in self.extracted.items()})
        return data


class ToolCallRecord(BaseModel):
    """Auditable tool call made by an agent."""

    agent: str
    tool: str
    action: str
    input_summary: Dict[str, Any] = Field(default_factory=dict)
    output_summary: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None


class AssertionResult(BaseModel):
    """Result of evaluating one business/status assertion."""

    description: str
    path: str = ""
    operator: str = ""
    expected: Optional[Union[str, int, float, bool]] = None
    actual: Optional[Any] = None
    passed: bool
    error: Optional[str] = None


class ExecutedRequest(BaseModel):
    """A concrete request attempt made by Agent 2."""

    step: int
    attempt: int
    name: str
    method: str
    url: str
    templated_path: str
    templated_headers: Optional[Dict[str, Any]] = None
    templated_query_params: Optional[Dict[str, Any]] = None
    templated_request_body: Optional[Dict[str, Any]] = None
    request_headers: Optional[Dict[str, Any]] = None
    request_params: Optional[Dict[str, Any]] = None
    request_body: Optional[Any] = None
    response_status: Optional[int] = None
    response_body: Optional[Any] = None
    business_check_results: List[AssertionResult] = Field(default_factory=list)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    status: Literal["passed", "failed"] = "failed"
    error: Optional[str] = None


class StepResult(BaseModel):
    """Final result for one scenario step."""

    step_num: int
    name: str
    status: Literal["passed", "failed", "skipped"]
    attempts: int = 1
    method: str = ""
    url: str = ""
    request_params: Optional[Dict[str, Any]] = None
    request_body: Optional[Any] = None
    response_status: Optional[int] = None
    response_body: Optional[Any] = None
    extracted_vars: Dict[str, Any] = Field(default_factory=dict)
    assertion_results: List[AssertionResult] = Field(default_factory=list)
    error: Optional[str] = None
    attempts_trace: List[ExecutedRequest] = Field(default_factory=list)


class ScenarioExecutionResult(BaseModel):
    """Aggregated result of running a full scenario."""

    scenario_name: str
    overall_status: Literal["passed", "failed"]
    reasoning_log: List[str] = Field(default_factory=list)
    total_steps: int
    passed_steps: int
    failed_steps: int
    steps: List[StepResult]
    ready_requests: List[ExecutedRequest] = Field(default_factory=list)
    variables: VariableContext = Field(default_factory=VariableContext)
    final_context: Dict[str, Any] = Field(default_factory=dict)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
