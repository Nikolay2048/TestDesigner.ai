from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict


class TestStep(BaseModel):
    """Single executable API test scenario step from knowledge base."""

    model_config = ConfigDict(populate_by_name=True)

    step: int = Field(description="Sequential step number inside the test scenario.")
    name: str = Field(description="Human-readable description of the test step.")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(description="HTTP method used for the request.")
    path: str = Field(description="Relative endpoint path. May contain templates like {{ variableName }}.")
    headers: Optional[Dict[str, Any]] = Field(default=None, description="HTTP headers. May contain templates.")
    request_body: Optional[Dict[str, Any]] = Field(default=None, description="JSON request body. May contain templates.")
    query_params: Optional[Dict[str, Any]] = Field(default=None, description="HTTP query parameters. May contain templates.")
    expected_status: int = Field(description="Expected HTTP status code. Non-2xx can be valid for negative tests.")
    assert_resp: List[str] = Field(default_factory=list, description="Assertions validated against the response.")


class RequestExecutionResult(BaseModel):
    """Raw HTTP request execution result."""

    status_code: Optional[int] = Field(default=None, description="Actual HTTP response status code.")
    response_json: Optional[Any] = Field(default=None, description="Parsed JSON response body, if available.")
    response_text: Optional[str] = Field(default=None, description="Raw response text if body is not JSON.")
    response_headers: Dict[str, Any] = Field(default_factory=dict, description="HTTP response headers.")
    error: Optional[str] = Field(default=None, description="Transport-level or execution error.")


class ExtractedVariable(BaseModel):
    """Variable extracted from previous API response."""

    extracted_value: Any = Field(description="Actual value extracted from previous response.")
    source_step: int = Field(description="Step number where the value was extracted.")
    extraction_expression: str = Field(description="JSON path/expression used to extract the value.")
    overwrite_reason: Optional[str] = Field(default=None, description="Reason why previous value was overwritten, if applicable.")


class GeneratedVariable(BaseModel):
    """Variable generated independently from previous responses."""

    generated_value: Any = Field(description="Actual generated value.")
    generator_name: str = Field(description="Generator or generation policy name.")
    generator_params: Dict[str, Any] = Field(default_factory=dict, description="Parameters used by the generator.")
    reason: Optional[str] = Field(default=None, description="Why this value was generated.")
    overwrite_reason: Optional[str] = Field(default=None, description="Reason why previous value was overwritten, if applicable.")
    generator_code_for_generation: Optional[str] = Field(default=None, description="Code generation used by the generator.")

class ExecutionContext(BaseModel):
    """Runtime variable memory shared between scenario steps."""

    variables: Dict[str, ExtractedVariable] = Field(default_factory=dict, description="Variables extracted from previous responses.")
    generated_variables: Dict[str, GeneratedVariable] = Field(default_factory=dict, description="Variables generated independently from previous responses.")


class ExecutedRequest(BaseModel):
    """Final resolved request that was actually sent to API."""

    step: int = Field(description="Step number.")
    name: str = Field(description="Step name.")
    method: str = Field(description="HTTP method.")
    url: str = Field(description="Absolute resolved URL actually called.")
    path: str = Field(description="Original relative path from scenario.")
    headers: Optional[Dict[str, Any]] = Field(default=None, description="Resolved request headers.")
    query_params: Optional[Dict[str, Any]] = Field(default=None, description="Resolved query parameters.")
    request_body: Optional[Dict[str, Any]] = Field(default=None, description="Resolved request body actually sent.")
    templated_request_body: Optional[Dict[str, Any]] = Field(default=None, description="Original body with templates for Postman export.")


class ExecutedStep(BaseModel):
    """Stored response and request for one executed step."""

    step: int = Field(description="Step number.")
    request: ExecutedRequest = Field(description="Resolved request actually sent.")
    result: RequestExecutionResult = Field(description="Raw response returned by API.")


class StabilizedRequest(BaseModel):
    """Request information needed to generate a Postman item."""

    step: int = Field(description="Step number.")
    name: str = Field(description="Step name.")
    method: str = Field(description="HTTP method.")
    path: str = Field(description="Relative request path for Postman collection.")
    headers: Optional[Dict[str, Any]] = Field(default=None, description="Request headers, preferably with templates.")
    query_params: Optional[Dict[str, Any]] = Field(default=None, description="Query params, preferably with templates.")
    resolved_request_body: Optional[Dict[str, Any]] = Field(default=None, description="Body that actually worked during stabilization.")
    templated_request_body: Optional[Dict[str, Any]] = Field(default=None, description="Body with variables for Postman collection.")
    expected_status: int = Field(description="Expected HTTP status code.")
    actual_status: Optional[int] = Field(default=None, description="Actual HTTP status code from final execution attempt.")
    passed: bool = Field(description="True if status and supported assertions passed.")


class ScenarioVariable(BaseModel):
    """Variable dependency needed for Postman generation."""

    name: str = Field(description="Variable name used in templates.")
    value: Any = Field(description="Runtime value observed during stabilization.")
    source_type: Literal["extracted", "generated"] = Field(description="Variable origin type.")
    source_step: Optional[int] = Field(default=None, description="Step where variable was extracted.")
    extraction_expression: Optional[str] = Field(default=None, description="Expression used to extract value from response.")
    generator_name: Optional[str] = Field(default=None, description="Generator used for generated variable.")
    generator_params: Dict[str, Any] = Field(default_factory=dict, description="Generator parameters.")
    used_in_steps: List[int] = Field(default_factory=list, description="Steps where this variable is used.")


class StabilizedScenarioResult(BaseModel):
    """Final stabilized scenario artifact used for Postman collection generation."""

    scenario_name: str = Field(description="Scenario name.")
    requests: List[StabilizedRequest] = Field(default_factory=list, description="Final request chain.")
    variables: List[ScenarioVariable] = Field(default_factory=list, description="Generated/extracted variable graph.")
    execution_context: ExecutionContext = Field(description="Final execution context.")
    proposals: List[str] = Field(default_factory=list, description="Scenario update proposals for human review.")


class ScenarioStabilizationInput(BaseModel):
    """Input for scenario stabilization."""

    scenario_name: str = Field(description="Human-readable scenario name.")
    base_url: str = Field(description="Base API URL, for example http://localhost:8080.")
    steps: List[TestStep] = Field(description="Scenario steps from knowledge base.")
    business_context: str = Field(default="", description="Business rules and scenario description.")
    swagger_context: Dict[str, Any] = Field(default_factory=dict, description="Relevant Swagger/OpenAPI fragments.")
    execution_context: ExecutionContext = Field(default_factory=ExecutionContext, description="Initial execution context.")


class GeneratedValueResult(BaseModel):
    """Output of the data generation agent."""

    value: Any = Field(description="Generated value.")
    source_type: Literal["generated"] = Field(default="generated", description="Always generated.")
    generator_name: str = Field(description="Generator or policy used.")
    generator_params: Dict[str, Any] = Field(default_factory=dict, description="Generation parameters.")
    reason: str = Field(description="Reason why this value was generated.")
