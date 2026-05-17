"""Domain models for the multi-agent API test designer."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
VarKind = Literal["constant", "generated", "extracted"]
AssertionOperator = Literal["eq", "ne", "exists", "not_null", "contains"]
CorrectionType = Literal[
    "request_body_patch",
    "query_params_patch",
    "headers_patch",
    "path_patch",
    "variable_source_change",
    "generation_constraints_patch",
    "extraction_expression_patch",
    "expected_status_patch",
    "assertion_patch",
    "step_skip",
]
CorrectionConfidence = Literal["low", "medium", "high"]


class ConstantVariable(BaseModel):
    type: Literal["constant"] = "constant"
    name: str
    value: Any
    description: str = ""


class GeneratedVariable(BaseModel):
    type: Literal["generated"] = "generated"
    name: str
    generated_value: Any
    generator_name: str
    generator_params: Dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    overwrite_reason: Optional[str] = None
    generator_function: Optional[str] = None


class ExtractedVariable(BaseModel):
    type: Literal["extracted"] = "extracted"
    name: str
    extracted_value: Any
    source_step: int
    extraction_expression: str
    overwrite_reason: Optional[str] = None


class VariableSource(BaseModel):
    name: str
    kind: VarKind
    description: str = ""
    locations: List[str] = Field(default_factory=list)
    source_step: Optional[int] = None
    extraction_expression: Optional[str] = None
    generation_goal: Optional[str] = None
    generation_requires: Dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None


class VariableBinding(BaseModel):
    """Planned usage of one template variable in a concrete request location."""

    name: str
    source: VarKind
    location: Literal["path", "path_param", "query", "header", "body"]
    field_path: str
    description: str = ""
    generation_goal: Optional[str] = None
    constraints: Dict[str, Any] = Field(default_factory=dict)
    source_step: Optional[int] = None
    extraction_expression: Optional[str] = None
    reason: Optional[str] = None


class VariableContext(BaseModel):
    constants: Dict[str, ConstantVariable] = Field(default_factory=dict)
    generated: Dict[str, GeneratedVariable] = Field(default_factory=dict)
    extracted: Dict[str, ExtractedVariable] = Field(default_factory=dict)

    def values(self) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        merged.update({k: v.value for k, v in self.constants.items()})
        merged.update({k: v.generated_value for k, v in self.generated.items()})
        merged.update({k: v.extracted_value for k, v in self.extracted.items()})
        return merged


class BusinessRule(BaseModel):
    id: str
    description: str
    applies_to_variables: List[str] = Field(default_factory=list)
    applies_to_steps: List[int] = Field(default_factory=list)


class Assertion(BaseModel):
    description: str
    path: str
    operator: AssertionOperator
    expected: Optional[Any] = None


class ExtractionRule(BaseModel):
    name: str
    expression: str
    required: bool = True
    description: str = ""
    source: Literal["agent1", "agent2"] = "agent1"


class TestStep(BaseModel):
    """Single executable API test scenario step."""

    model_config = ConfigDict(populate_by_name=True)

    step: int
    name: str
    method: HttpMethod
    path: str
    headers: Optional[Dict[str, Any]] = None
    request_body: Optional[Dict[str, Any]] = None
    query_params: Optional[Dict[str, Any]] = None
    path_params: Dict[str, Any] = Field(default_factory=dict)
    expected_status: int
    success_criteria: List[str] = Field(default_factory=list)
    variable_bindings: List[VariableBinding] = Field(default_factory=list)
    extract: List[ExtractionRule] = Field(default_factory=list)
    assertions: List[Assertion] = Field(default_factory=list)
    swagger_operation_id: Optional[str] = None
    swagger_notes: Dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class ScenarioCard(BaseModel):
    scenario_name: str
    business_context: str
    business_rules: List[BusinessRule] = Field(default_factory=list)
    constant_variables: Dict[str, Any] = Field(default_factory=dict)
    constant_descriptions: Dict[str, str] = Field(default_factory=dict)
    variable_sources: Dict[str, VariableSource] = Field(default_factory=dict)
    steps: List[TestStep]


class ParameterInfo(BaseModel):
    name: str
    location: Literal["query", "path", "header", "cookie"]
    required: bool = False
    description: str = ""
    schema_: Dict[str, Any] = Field(default_factory=dict, alias="schema")
    example: Any = None


class RequestExample(BaseModel):
    endpoint: str
    method: HttpMethod
    headers: Dict[str, Any] = Field(default_factory=dict)
    query_params: Dict[str, Any] = Field(default_factory=dict)
    path_params: Dict[str, Any] = Field(default_factory=dict)
    json_body: Optional[Any] = None
    comments: List[str] = Field(default_factory=list)


class ResponseInfo(BaseModel):
    status_code: str
    description: str = ""
    content_type: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(default=None, alias="schema")
    example: Any = None


class EndpointInfo(BaseModel):
    method: HttpMethod
    path: str
    operation_id: Optional[str] = None
    summary: str = ""
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    parameters: List[ParameterInfo] = Field(default_factory=list)
    request_schema: Optional[Dict[str, Any]] = None
    request_example: RequestExample
    responses: List[ResponseInfo] = Field(default_factory=list)


class OpenApiCatalog(BaseModel):
    title: str = ""
    version: str = ""
    base_url: str = ""
    endpoints: List[EndpointInfo] = Field(default_factory=list)

    def find(self, method: str, path: str) -> Optional[EndpointInfo]:
        for endpoint in self.endpoints:
            if endpoint.method == method.upper() and endpoint.path == path:
                return endpoint
        return None


class RequestBodyPatch(BaseModel):
    """Suggested correction from Agent 2 LLM error analysis."""

    fixable: bool
    reasoning: str
    body_patch: Dict[str, Any] = Field(default_factory=dict)


class ToolTrace(BaseModel):
    agent: str
    tool: str
    action: str
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None


class CheckResult(BaseModel):
    description: str
    passed: bool
    actual: Any = None
    expected: Any = None
    error: Optional[str] = None


class RequestRecord(BaseModel):
    step: int
    attempt: int
    name: str
    method: HttpMethod
    url: str
    templated_path: str
    templated_headers: Optional[Dict[str, Any]] = None
    templated_query_params: Optional[Dict[str, Any]] = None
    templated_request_body: Optional[Dict[str, Any]] = None
    request_headers: Dict[str, Any] = Field(default_factory=dict)
    request_query_params: Optional[Dict[str, Any]] = None
    request_body: Any = None
    response_status: Optional[int] = None
    response_body: Any = None
    checks: List[CheckResult] = Field(default_factory=list)
    extract: List[ExtractionRule] = Field(default_factory=list)
    assertions: List[Assertion] = Field(default_factory=list)
    status: Literal["passed", "failed"] = "failed"
    error: Optional[str] = None


class ScenarioCorrection(BaseModel):
    step: int
    correction_type: CorrectionType
    target: str
    before: Any = None
    after: Any = None
    reason: str
    evidence: Dict[str, Any] = Field(default_factory=dict)
    confidence: CorrectionConfidence = "medium"
    applied: bool = True


class StepExecution(BaseModel):
    step: int
    name: str
    status: Literal["passed", "failed", "skipped"]
    attempts: int = 0
    final_request: Optional[RequestRecord] = None
    attempt_history: List[RequestRecord] = Field(default_factory=list)
    error: Optional[str] = None


class ExecutionReport(BaseModel):
    scenario_name: str
    status: Literal["passed", "failed"]
    reasoning: List[str] = Field(default_factory=list)
    steps: List[StepExecution] = Field(default_factory=list)
    successful_requests: List[RequestRecord] = Field(default_factory=list)
    variables: VariableContext
    corrections: List[ScenarioCorrection] = Field(default_factory=list)
    traces: List[ToolTrace] = Field(default_factory=list)
