from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from testdesigner_ai.domain.evidence import EvidenceRef, ReviewState


class SourceDocument(BaseModel):
    source_id: str
    source_type: Literal[
        "system_analysis",
        "business_requirements",
        "acceptance_criteria",
    ]
    title: str
    content: str
    version: str | None = None


class RawEndpointMention(BaseModel):
    """Endpoint-like text produced by deterministic extraction."""

    mention_id: str
    method: str | None = None
    path: str
    line_number: int
    raw_text: str


class DocumentationAnalysisInput(BaseModel):
    documents: list[SourceDocument]
    endpoint_mentions: list[RawEndpointMention] = Field(default_factory=list)


class BusinessAction(BaseModel):
    action_id: str
    document_step_id: str
    order: int
    text: str
    evidence: list[EvidenceRef]


class DocumentedFact(BaseModel):
    text: str
    evidence: list[EvidenceRef]


class ScenarioDependency(BaseModel):
    kind: Literal["requires_scenario", "requires_state", "requires_data"]
    reference: str | None = None
    required_state: str | None = None
    required_data: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef]


class EndpointClassification(BaseModel):
    """Semantic classification only; method and path stay deterministic."""

    mention_id: str
    related_action_id: str | None = None
    location: Literal["header", "step", "rule", "unknown"] = "unknown"


class ValidationIssue(BaseModel):
    stage: Literal["json", "schema", "referential", "semantic"]
    code: str
    message: str
    path: str | None = None
    severity: Literal["error", "warning"]
    evidence: list[EvidenceRef] = Field(default_factory=list)


class OpenQuestion(BaseModel):
    question: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


class DocumentationAnalysisOutput(BaseModel):
    actions: list[BusinessAction] = Field(default_factory=list)
    business_rules: list[DocumentedFact] = Field(default_factory=list)
    success_criteria: list[DocumentedFact] = Field(default_factory=list)
    explicit_negative_conditions: list[DocumentedFact] = Field(default_factory=list)
    scenario_dependencies: list[ScenarioDependency] = Field(default_factory=list)
    endpoint_classifications: list[EndpointClassification] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    review: ReviewState = Field(default_factory=ReviewState)
