from __future__ import annotations

from typing import Literal

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
    understanding: ScenarioUnderstanding | None = None
    endpoint_mapping: EndpointMappingResult | None = None
    flow: FlowDraft | None = None
    agent_runs: list[AgentRun] = Field(default_factory=list)
