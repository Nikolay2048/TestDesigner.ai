from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


# === EXECUTOR ===
class TestStep(BaseModel):
    """Single executable API test scenario step."""

    step: int = Field(description="Sequential step number inside the test scenario.")
    name: str = Field(description="Human-readable description of the test step.")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(description="HTTP method used for the request.")
    path: str = Field(description="Relative REST endpoint path. May contain template variables.")
    headers: Optional[Dict[str, str]] = (Field(default=None,
                                               description="HTTP headers sent with the request. May contain template variables."))
    request_body: Optional[Dict[str, Any]] = Field(default=None, description="JSON request body sent to the API.")
    query_params: Optional[Dict[str, Any]] = Field(default=None, description="HTTP query parameters.")
    expected_status: int = Field(description="Expected HTTP response status code.")

    assert_resp: List[str] = Field(
        default_factory=list,
        description="Assertions validated against the response."
    )


class RequestExecutionResult(BaseModel):
    """Raw HTTP request execution result."""

    status_code: Optional[int] = Field(default=None, description="Actual HTTP response status code.")
    response_json: Optional[Any] = Field(default=None, description="Parsed JSON response body.")
    response_text: Optional[str] = Field(default=None, description="Raw response text if response is not JSON.")
    error: Optional[str] = Field(default=None, description="Transport-level or execution error.")


class StepExecutionReport(BaseModel):
    """Final execution report for a test step."""

    step: int = Field(description="Executed step number.")
    name: str = Field(description="Executed step name.")
    passed: bool = Field(description="True if actual result matches expected result.")
    attempts: int = Field(description="Number of execution attempts including retries.")
    expected_status: int = Field(description="Expected HTTP response status code.")
    actual_status: Optional[int] = Field(default=None, description="Actual HTTP response status code.")

    changes: List[str] = Field(
        default_factory=list,
        description="Automatic corrections applied by the agent."
    )

    scenario_update_required: bool = Field(
        default=False,
        description="True if the agent proposes updating the original test scenario."
    )

    scenario_update_proposal: Optional[str] = Field(
        default=None,
        description="Human-readable proposal for scenario update."
    )

    response: Optional[Any] = Field(default=None, description="Final API response payload.")
    error: Optional[str] = Field(default=None, description="Execution or validation error.")


# === CONTEXT ===
class ExtractedVariable(BaseModel):
    extracted_value: Any = Field(description="Actual value extracted from previous response.")
    source_step: int = Field(description="Step number where the value was extracted.")
    extraction_expression: str = Field(description="JSON path used to extract the value.")


class GeneratedVariable(BaseModel):
    generated_value: Any = Field(description="Actual generated value.")
    generator_name: str = Field(description="Generator used to create the value.")
    generator_params: Dict[str, Any] = Field(default_factory=dict, description="Parameters used by the generator.")


class ExecutionContext(BaseModel):
    variables: Dict[str, ExtractedVariable] = Field(default_factory=dict,
                                                    description="Variables extracted from previous responses.")
    generated_variables: Dict[str, GeneratedVariable] = Field(default_factory=dict,
                                                              description="Variables generated independently from previous responses.")


# === SCENARIO ===
class ScenarioExecutionReport(BaseModel):
    """Final execution report for the full API scenario."""

    scenario_name: str = Field(description="Human-readable scenario name.")
    passed: bool = Field(description="True if all scenario steps passed successfully.")
    total_steps: int = Field(description="Total number of executed scenario steps.")
    passed_steps: int = Field(description="Number of successfully passed steps.")
    failed_step: Optional[int] = Field(default=None,
                                       description="First failed step number if scenario execution failed.")
    step_reports: List[StepExecutionReport] = Field(default_factory=list,
                                                    description="Execution reports for all scenario steps.")
    execution_context: ExecutionContext = Field(description="Final execution context with extracted runtime variables.")
    scenario_update_required: bool = Field(default=False,
                                           description="True if agent proposes updates to the original scenario.")
    scenario_update_proposals: List[str] = Field(default_factory=list,
                                                 description="List of proposed scenario updates generated during execution.")

class ScenarioStabilizationInput(BaseModel):
    scenario_name: str = Field(description="Human-readable scenario name.")
    base_url: str = Field(description="Base API URL, for example http://localhost:8080.")
    steps: List[TestStep] = Field(description="Scenario steps from knowledge base.")
    execution_context: ExecutionContext = Field(default_factory=ExecutionContext)
