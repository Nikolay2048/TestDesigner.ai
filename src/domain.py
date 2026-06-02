from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ApiOperation(BaseModel):
    """Compact OpenAPI operation summary for future flow planning."""

    method: str
    path: str
    operation_id: str
    summary: str = ""
    request_schema: dict | None = None
    response_schemas: dict[str, dict] = Field(default_factory=dict)
    response_statuses: list[str] = Field(default_factory=list)


class RawEndpointMention(BaseModel):
    """Endpoint-like text found by deterministic regex extraction."""

    method: str | None = None
    path: str
    raw_text: str
    line_number: int


class ScenarioInput(BaseModel):
    path: str
    title: str
    text: str
    raw_endpoint_mentions: list[RawEndpointMention] = Field(default_factory=list)


class EndpointMention(BaseModel):
    """Endpoint written directly in the analyst documentation."""

    method: str | None = None
    path: str
    location: Literal["header", "step", "rule", "unknown"] = "unknown"
    related_step: str | None = None
    note: str = ""


class ScenarioDependency(BaseModel):
    """Precondition that may require another scenario, state, or data setup."""

    kind: Literal["requires_scenario", "requires_state", "requires_data"]
    reference: str | None = None
    required_state: str | None = None
    required_data: list[str] = Field(default_factory=list)
    reason: str = ""


class ScenarioUnderstanding(BaseModel):
    """Structured business understanding extracted from the scenario text."""

    title: str
    goal: str = ""
    actors: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    business_steps: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    negative_conditions: list[str] = Field(default_factory=list)
    endpoint_mentions: list[EndpointMention] = Field(default_factory=list)
    scenario_dependencies: list[ScenarioDependency] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class FlowDraft(BaseModel):
    """Future placeholder: ordered REST flow before request bodies and variables."""

    steps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OperationRef(BaseModel):
    method: str
    path: str


class StepOperationMapping(BaseModel):
    business_step: str
    operations: list[OperationRef] = Field(default_factory=list)
    source: Literal["explicit_mention", "semantic_match", "none"] = "none"
    confidence: Literal["high", "medium", "low", "none"] = "low"
    reason: str = ""
    risks: list[str] = Field(default_factory=list)


class UnmappedStep(BaseModel):
    business_step: str
    reason: str


class EndpointMappingResult(BaseModel):
    mappings: list[StepOperationMapping] = Field(default_factory=list)
    unmapped_steps: list[UnmappedStep] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class GeneratorSpec(BaseModel):
    """Generator available both to Python executor and future Postman export."""

    name: str
    description: str = ""
    parameters: dict[str, str] = Field(default_factory=dict)


class RequestValueBinding(BaseModel):
    """How to fill one request value before a REST call."""

    target: str
    location: Literal["path", "query", "header", "body"]
    source: Literal["static", "generated", "response", "computed", "literal", "unknown"]
    variable: str | None = None
    static_key: str | None = None
    generator: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    json_path: str | None = None
    expression: str | None = None
    literal: Any = None
    scope: Literal["step", "scenario"] = "step"
    source_step_id: str | None = None
    candidate_id: str | None = None
    policy: str = ""
    requires_human_review: bool = False
    reason: str = ""


class ResponseExtraction(BaseModel):
    """Variable to save from a response after a REST call."""

    variable: str
    json_path: str
    scope: Literal["step", "scenario"] = "scenario"
    source_step_id: str | None = None
    candidate_id: str | None = None
    policy: str = ""
    required: bool = True
    reason: str = ""


class StepDataBinding(BaseModel):
    business_step: str
    operation: OperationRef
    request_bindings: list[RequestValueBinding] = Field(default_factory=list)
    response_extractions: list[ResponseExtraction] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class MissingGenerator(BaseModel):
    name: str
    reason: str
    suggested_parameters: dict[str, str] = Field(default_factory=dict)
    human_review_required: bool = True


class DataBindingPlan(BaseModel):
    """Data plan for executor and later Postman script generation."""

    steps: list[StepDataBinding] = Field(default_factory=list)
    missing_generators: list[MissingGenerator] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class DataNeed(BaseModel):
    step_id: str
    target: str
    location: Literal["path", "query", "header", "body"]
    type: str = "unknown"
    required: bool = True
    field_schema: dict[str, Any] = Field(default_factory=dict)


class DataProducer(BaseModel):
    step_id: str
    json_path: str
    type: str = "unknown"
    field_name: str = ""
    operation: OperationRef


class DataDependencyStep(BaseModel):
    step_id: str
    business_step: str
    operation: OperationRef
    needs: list[DataNeed] = Field(default_factory=list)
    produces: list[DataProducer] = Field(default_factory=list)


class DataDependencyGraph(BaseModel):
    steps: list[DataDependencyStep] = Field(default_factory=list)


class ResponseCandidate(BaseModel):
    candidate_id: str
    target: str
    source_step_id: str
    json_path: str
    type: str = "unknown"
    producer_operation: OperationRef
    reason: str = ""


class DependencyResolutionTask(BaseModel):
    step_id: str
    business_step: str
    operation: OperationRef
    need: DataNeed
    candidates: list[ResponseCandidate] = Field(default_factory=list)


class DependencyResolution(BaseModel):
    step_id: str
    target: str
    selected_candidate_id: str | None = None
    confidence: Literal["high", "medium", "low", "none"] = "none"
    reason: str = ""


class DependencyResolverResult(BaseModel):
    resolutions: list[DependencyResolution] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class GenerationBindingTask(BaseModel):
    step_id: str
    business_step: str
    operation: OperationRef
    need: DataNeed
    static_keys: list[str] = Field(default_factory=list)
    available_generators: list[GeneratorSpec] = Field(default_factory=list)
    business_context: list[str] = Field(default_factory=list)


class GenerationBindingDecision(BaseModel):
    step_id: str
    target: str
    source: Literal["static", "generated", "computed", "literal", "missing", "unknown"]
    static_key: str | None = None
    generator: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    expression: str | None = None
    literal: Any = None
    confidence: Literal["high", "medium", "low", "none"] = "none"
    reason: str = ""
    requires_human_review: bool = False


class GenerationBindingResult(BaseModel):
    decisions: list[GenerationBindingDecision] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class AgentMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class AgentRun(BaseModel):
    agent_name: str
    prompt: list[AgentMessage] = Field(default_factory=list)
    output: dict | None = None
    status: Literal["completed", "needs_llm", "stub", "failed"] = "stub"
    notes: list[str] = Field(default_factory=list)


class ProjectState(BaseModel):
    scenario: ScenarioInput
    operations: list[ApiOperation] = Field(default_factory=list)
    static_test_data: dict[str, Any] = Field(default_factory=dict)
    understanding: ScenarioUnderstanding | None = None
    endpoint_mapping: EndpointMappingResult | None = None
    data_dependency_graph: DataDependencyGraph | None = None
    dependency_resolutions: DependencyResolverResult | None = None
    generation_bindings: GenerationBindingResult | None = None
    data_binding: DataBindingPlan | None = None
    flow: FlowDraft | None = None
    agent_runs: list[AgentRun] = Field(default_factory=list)
