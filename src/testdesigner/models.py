"""Core domain models for REST scenario design and execution."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
VariableKind = Literal["constant", "generated", "extracted", "literal"]
StepStatus = Literal["passed", "failed", "skipped"]


class Parameter(BaseModel):
    name: str
    location: Literal["query", "path", "header", "cookie"]
    required: bool = False
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    example: Any = None
    description: str = ""


class ResponseSpec(BaseModel):
    status_code: str
    description: str = ""
    content_type: str | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    example: Any = None


class EndpointSpec(BaseModel):
    method: HttpMethod
    path: str
    operation_id: str | None = None
    summary: str = ""
    description: str = ""
    parameters: list[Parameter] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None
    request_example: Any = None
    responses: list[ResponseSpec] = Field(default_factory=list)

    def response(self, status: int) -> ResponseSpec | None:
        for item in self.responses:
            if item.status_code == str(status):
                return item
        return None


class OpenApiContract(BaseModel):
    title: str = ""
    version: str = ""
    base_url: str = ""
    endpoints: list[EndpointSpec] = Field(default_factory=list)

    def find(self, method: str, path: str) -> EndpointSpec | None:
        for endpoint in self.endpoints:
            if endpoint.method == method.upper() and endpoint.path == path:
                return endpoint
        return None


class ParsedScenarioStep(BaseModel):
    index: int
    method: HttpMethod
    path: str
    text: str = ""
    expected_status: int | None = None
    request_body: Any = None
    query_params: dict[str, Any] = Field(default_factory=dict)


class ParsedScenario(BaseModel):
    title: str
    raw_text: str
    steps: list[ParsedScenarioStep] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)


class VariableSource(BaseModel):
    name: str
    kind: VariableKind
    value: Any = None
    source_step: int | None = None
    extraction: str | None = None
    generator: str | None = None
    locations: list[str] = Field(default_factory=list)
    description: str = ""


class ExtractionRule(BaseModel):
    name: str
    expression: str
    required: bool = True


class AssertionRule(BaseModel):
    description: str
    expression: str
    operator: Literal["exists", "not_null", "eq", "contains"] = "exists"
    expected: Any = None


class ScenarioStep(BaseModel):
    step: int
    name: str
    method: HttpMethod
    path: str
    expected_status: int
    headers: dict[str, Any] = Field(default_factory=dict)
    query_params: dict[str, Any] = Field(default_factory=dict)
    path_params: dict[str, Any] = Field(default_factory=dict)
    request_body: Any = None
    extract: list[ExtractionRule] = Field(default_factory=list)
    assertions: list[AssertionRule] = Field(default_factory=list)
    notes: str = ""


class ScenarioCard(BaseModel):
    name: str
    description: str = ""
    steps: list[ScenarioStep] = Field(default_factory=list)
    variables: dict[str, VariableSource] = Field(default_factory=dict)
    business_rules: list[str] = Field(default_factory=list)


class RuntimeValue(BaseModel):
    name: str
    kind: VariableKind
    value: Any
    source_step: int | None = None
    expression: str | None = None
    generator: str | None = None


class RequestAttempt(BaseModel):
    step: int
    attempt: int
    method: HttpMethod
    url: str
    headers: dict[str, Any] = Field(default_factory=dict)
    query_params: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    response_status: int | None = None
    response_body: Any = None
    error: str | None = None


class StepCheck(BaseModel):
    description: str
    passed: bool
    expected: Any = None
    actual: Any = None
    error: str | None = None


class StepExecution(BaseModel):
    step: int
    name: str
    status: StepStatus
    attempts: list[RequestAttempt] = Field(default_factory=list)
    checks: list[StepCheck] = Field(default_factory=list)
    error: str | None = None


class ScenarioPatch(BaseModel):
    step: int
    target: str
    before: Any = None
    after: Any = None
    reason: str
    applied: bool = True


class AgentLogEntry(BaseModel):
    agent: str
    action: str
    status: str = "ok"
    step: int | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ExecutionReport(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    scenario_name: str
    status: Literal["passed", "failed"]
    steps: list[StepExecution] = Field(default_factory=list)
    runtime_values: dict[str, RuntimeValue] = Field(default_factory=dict)
    patches: list[ScenarioPatch] = Field(default_factory=list)
    agent_log: list[AgentLogEntry] = Field(default_factory=list)
