from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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

    @field_validator("required_data", mode="before")
    @classmethod
    def empty_required_data_when_null(cls, value):
        return [] if value is None else value


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
    source: Literal["static", "generated", "response", "external_context", "computed", "literal", "unknown"]
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


class ResolvedBindingTrace(BaseModel):
    target: str
    source: str
    variable: str | None = None
    static_key: str | None = None
    generator: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    expression: str | None = None
    value_preview: Any = None
    policy: str = ""
    error: str | None = None


class ExecutorStepTrace(BaseModel):
    step_id: str
    business_step: str
    operation: OperationRef
    resolved_path: str
    resolved_bindings: list[ResolvedBindingTrace] = Field(default_factory=list)
    request: dict[str, Any] = Field(default_factory=dict)
    response_status: int | None = None
    response_body: Any = None
    extracted_variables: dict[str, Any] = Field(default_factory=dict)
    status: Literal["passed", "failed"] = "failed"
    failure: str | None = None


class ExecutorTrace(BaseModel):
    attempt: int
    base_url: str
    steps: list[ExecutorStepTrace] = Field(default_factory=list)
    status: Literal["passed", "failed"] = "failed"
    failed_step_id: str | None = None
    failure: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)


class StabilizationDiagnosis(BaseModel):
    attempt: int
    failed_step_id: str
    failure_type: Literal[
        "missing_request_data",
        "invalid_request_data",
        "bad_response_extraction",
        "http_error",
        "unknown",
    ] = "unknown"
    summary: str
    evidence: list[str] = Field(default_factory=list)
    suspected_bindings: list[dict[str, Any]] = Field(default_factory=list)
    recommended_fix_type: str | None = None
    confidence: Literal["high", "medium", "low", "none"] = "none"
    requires_human_review: bool = True


class BindingPatch(BaseModel):
    patch_type: Literal[
        "use_existing_variable",
        "replace_request_binding",
        "replace_response_extraction",
        "add_response_extraction",
        "replace_generated_params",
        "replace_computed_expression",
        "no_patch",
    ]
    step_id: str | None = None
    target: str | None = None
    variable: str | None = None
    new_binding: RequestValueBinding | None = None
    new_extraction: ResponseExtraction | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    expression: str | None = None
    reason: str = ""
    why_not_repeating_previous_fix: str = ""
    requires_human_review: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_simple_variable_patch(cls, value):
        if not isinstance(value, dict):
            return value
        if (
            value.get("patch_type") == "replace_request_binding"
            and isinstance(value.get("new_binding"), str)
        ):
            value = value.copy()
            value["patch_type"] = "use_existing_variable"
            value["variable"] = value.get("variable") or value["new_binding"]
            value["new_binding"] = None
        elif (
            value.get("patch_type") != "replace_request_binding"
            and isinstance(value.get("new_binding"), str)
        ):
            value = value.copy()
            value["new_binding"] = None
        return value


class StabilizationFix(BaseModel):
    attempt: int
    patches: list[BindingPatch] = Field(default_factory=list)
    reason: str = ""
    risks: list[str] = Field(default_factory=list)


class StabilizationAttempt(BaseModel):
    attempt: int
    trace: ExecutorTrace
    diagnosis: StabilizationDiagnosis | None = None
    fix: StabilizationFix | None = None
    applied_patches: list[BindingPatch] = Field(default_factory=list)


class StabilizationResult(BaseModel):
    status: Literal["passed", "failed"] = "failed"
    attempts: list[StabilizationAttempt] = Field(default_factory=list)
    stable_plan: DataBindingPlan | None = None
    review_required: bool = False
    review_notes: list[str] = Field(default_factory=list)


class ProvidedState(BaseModel):
    name: str
    value: Any
    semantic_type: str | None = None
    source_scenario: str
    source_step_id: str | None = None
    json_path: str | None = None


class ScenarioRunOutput(BaseModel):
    scenario_path: str
    status: Literal["passed", "failed"]
    provided_state: list[ProvidedState] = Field(default_factory=list)
    stable_plan: DataBindingPlan | None = None


class TestDesignField(BaseModel):
    step_id: str
    business_step: str
    operation: OperationRef
    target: str
    location: Literal["path", "query", "header", "body"]
    type: str = "unknown"
    required: bool = True
    field_schema: dict[str, Any] = Field(default_factory=dict)
    happy_value: Any = None
    binding_source: str = ""
    techniques: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class TestDesignRule(BaseModel):
    rule_id: str
    text: str
    related_step_ids: list[str] = Field(default_factory=list)
    related_targets: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class TestBasis(BaseModel):
    fields: list[TestDesignField] = Field(default_factory=list)
    rules: list[TestDesignRule] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class TestMutation(BaseModel):
    step_id: str
    target: str
    action: Literal["set_value", "omit_field"]
    value: Any = None


class TestExpectation(BaseModel):
    status: int | None = None
    accepted_statuses: list[int] = Field(default_factory=list)
    error_code: str | None = None
    description: str = ""
    source: Literal["openapi", "trace", "inferred", "human_review"] = "inferred"


class TestIdea(BaseModel):
    idea_id: str
    title: str
    type: Literal["positive", "negative"] = "negative"
    technique: str
    source: str
    mutation: TestMutation
    expected: TestExpectation
    requires_human_review: bool = True
    reason: str = ""


class TestIdeaRefinement(BaseModel):
    idea_id: str
    title: str | None = None
    reason: str | None = None
    expected_description: str | None = None
    requires_human_review: bool | None = None


class TestIdeaRefinementResult(BaseModel):
    refinements: list[TestIdeaRefinement] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class DesignedTestCase(BaseModel):
    case_id: str
    title: str
    type: Literal["positive", "negative"] = "negative"
    technique: str
    priority: Literal["high", "medium", "low"] = "medium"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_result: list[str] = Field(default_factory=list)
    setup_until_step: str | None = None
    mutated_step_id: str
    mutation: TestMutation
    expected: TestExpectation
    traceability: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    requires_human_review: bool = True


class TestCaseExecutionRecord(BaseModel):
    case_id: str
    status: Literal["not_run", "passed", "failed", "review_required", "blocked", "contract_mismatch"] = "not_run"
    mode: Literal["planned", "http"] = "planned"
    setup_until_step: str | None = None
    mutated_step_id: str
    expected_status: int | None = None
    accepted_statuses: list[int] = Field(default_factory=list)
    actual_status: int | None = None
    trace: ExecutorTrace | None = None
    notes: list[str] = Field(default_factory=list)


class TestDesignResult(BaseModel):
    basis: TestBasis
    ideas: list[TestIdea] = Field(default_factory=list)
    test_cases: list[DesignedTestCase] = Field(default_factory=list)
    executions: list[TestCaseExecutionRecord] = Field(default_factory=list)
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
    external_context_keys: list[str] = Field(default_factory=list)
    available_generators: list[GeneratorSpec] = Field(default_factory=list)
    business_context: list[str] = Field(default_factory=list)


class GenerationBindingDecision(BaseModel):
    step_id: str
    target: str
    source: Literal["static", "generated", "external_context", "computed", "literal", "missing", "unknown"]
    static_key: str | None = None
    external_key: str | None = None
    generator: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    expression: str | None = None
    literal: Any = None
    confidence: Literal["high", "medium", "low", "none"] = "none"
    reason: str = ""
    requires_human_review: bool = False

    @field_validator("params", mode="before")
    @classmethod
    def empty_params_when_null(cls, value):
        return {} if value is None else value


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
    external_context: dict[str, Any] = Field(default_factory=dict)
    understanding: ScenarioUnderstanding | None = None
    endpoint_mapping: EndpointMappingResult | None = None
    data_dependency_graph: DataDependencyGraph | None = None
    dependency_resolutions: DependencyResolverResult | None = None
    generation_bindings: GenerationBindingResult | None = None
    data_binding: DataBindingPlan | None = None
    stabilization: StabilizationResult | None = None
    test_design: TestDesignResult | None = None
    flow: FlowDraft | None = None
    agent_runs: list[AgentRun] = Field(default_factory=list)
