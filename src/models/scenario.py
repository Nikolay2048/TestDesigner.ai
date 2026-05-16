"""Scenario contracts shared by all agents.

Agent 1 produces :class:`ScenarioStabilizationInput`. Agent 2 treats that
object as an executable plan. The model intentionally keeps the original
business-oriented fields from the prompt and adds machine-facing metadata:
assertions, extraction rules, variable sources, and OpenAPI endpoint linkage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, computed_field

HTTPMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
AssertionOperator = Literal["eq", "ne", "exists", "not_null", "contains"]
VariableKind = Literal["constant", "generated", "extracted", "unknown"]


class BusinessRule(BaseModel):
    """Business rule inferred from the system-analysis document."""

    id: str = Field(description="Stable rule identifier, for example BR-001.")
    description: str = Field(description="Human-readable business rule.")
    applies_to_variables: List[str] = Field(default_factory=list)
    applies_to_steps: List[int] = Field(default_factory=list)


class VariableSource(BaseModel):
    """Where a template variable should come from at runtime."""

    name: str
    kind: VariableKind
    description: Optional[str] = None
    source_step: Optional[int] = None
    extraction_expression: Optional[str] = None
    generation_goal: Optional[str] = None
    generation_requires: Dict[str, Any] = Field(default_factory=dict)


class Assertion(BaseModel):
    """Machine-checkable assertion against a response body."""

    description: str
    path: str = Field(description="JSONPath expression, e.g. $.status.")
    operator: AssertionOperator
    expected: Optional[Union[str, int, float, bool]] = None


class ExtractionRule(BaseModel):
    """A variable extraction rule for a successful response."""

    name: str
    expression: str = Field(description="JSONPath expression.")
    description: Optional[str] = None
    required: bool = True


class TestStep(BaseModel):
    """Single executable API test scenario step."""

    model_config = ConfigDict(populate_by_name=True)

    step: int = Field(alias="step_num", description="Sequential step number.")
    name: str
    method: HTTPMethod
    path: str = Field(description="Relative endpoint path. May contain templates.")
    headers: Optional[Dict[str, Any]] = None
    request_body: Optional[Dict[str, Any]] = Field(default=None, alias="body")
    query_params: Optional[Dict[str, Any]] = None
    path_params: Dict[str, Any] = Field(default_factory=dict)
    expected_status: int = Field(alias="expected_status_code")
    success_criteria: List[str] = Field(default_factory=list)

    extract_variables: List[ExtractionRule] = Field(default_factory=list)
    assertions: List[Assertion] = Field(default_factory=list)
    variable_sources: List[VariableSource] = Field(default_factory=list)
    endpoint_operation_id: Optional[str] = None
    description: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def step_num(self) -> int:
        """Backward-compatible name used by older scripts."""
        return self.step

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expected_status_code(self) -> int:
        """Backward-compatible name used by older scripts."""
        return self.expected_status

    @computed_field  # type: ignore[prop-decorator]
    @property
    def body(self) -> Optional[Dict[str, Any]]:
        """Backward-compatible name used by older scripts."""
        return self.request_body

    @computed_field  # type: ignore[prop-decorator]
    @property
    def extract_vars(self) -> Dict[str, str]:
        """Backward-compatible extraction mapping."""
        return {rule.name: rule.expression for rule in self.extract_variables}


class ScenarioStabilizationInput(BaseModel):
    """Complete scenario card produced by Agent 1 and executed by Agent 2."""

    scenario_name: str
    business_context: str = ""
    business_rules: List[BusinessRule] = Field(default_factory=list)
    constant_variables: Dict[str, Any] = Field(default_factory=dict)
    steps: List[TestStep]
    variable_sources: List[VariableSource] = Field(default_factory=list)
    description: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def var_sources(self) -> List[VariableSource]:
        """Backward-compatible alias for older generators."""
        return self.variable_sources
