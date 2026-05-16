"""
Data models for Agent 1 output and Agent 2 input.

Agent 1 (ScenarioBuilderAgent) reads a business scenario and an API spec,
then produces a :class:`ScenarioStabilizationInput` — an ordered list of
:class:`TestStep` objects that Agent 2 can execute against a real server.

Variable reference convention
------------------------------
Dynamic values (extracted from previous responses or generated at runtime)
are represented as ``{{variableName}}`` string placeholders, e.g.::

    {"bookingId": "{{bookingId}}", "city": "{{city}}"}

Agent 2 substitutes these placeholders with actual values from its context
before each HTTP request.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Assertion
# ---------------------------------------------------------------------------

AssertionOperator = Literal["eq", "ne", "exists", "not_null", "contains"]


class Assertion(BaseModel):
    """A single check to validate against an API response body."""

    description: str = Field(
        description="Human-readable description of what is being checked."
    )
    path: str = Field(
        description=(
            "JSONPath expression pointing to the value under test. "
            "Use '$' for the root object. "
            "Examples: '$.status', '$.bookingId', '$.items[0].vehicleId'."
        )
    )
    operator: AssertionOperator = Field(
        description=(
            "Comparison operator. "
            "'eq' — equals expected; "
            "'ne' — not equals; "
            "'exists' — key is present (expected not needed); "
            "'not_null' — value is not null (expected not needed); "
            "'contains' — string or array contains expected."
        )
    )
    expected: Optional[Union[str, int, float, bool]] = Field(
        default=None,
        description=(
            "Expected scalar value — string, number, or boolean. "
            "Not required for 'exists' and 'not_null'. "
            "Example: 'CREATED', 1, true. NEVER use a dict or list here."
        ),
    )


# ---------------------------------------------------------------------------
# TestStep
# ---------------------------------------------------------------------------


class TestStep(BaseModel):
    """
    One executable test step — a single HTTP request with pre/post conditions.

    Path parameters and body values may use ``{{variableName}}`` placeholders
    that Agent 2 resolves from its runtime context before sending the request.
    """

    step_num: int = Field(description="Sequential step number starting from 1.")
    name: str = Field(description="Short human-readable step name.")
    description: str = Field(description="What this step does and why it matters.")

    method: str = Field(
        description="HTTP method in uppercase: GET, POST, PUT, PATCH, DELETE."
    )
    path: str = Field(
        description=(
            "API path template as defined in the spec, "
            "e.g. '/v1/bookings/{bookingId}'."
        )
    )

    path_params: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Values for path template parameters. "
            "Use {{varName}} for runtime variables. "
            "Example: {'bookingId': '{{bookingId}}'}."
        ),
    )
    query_params: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Query string parameters. "
            "Use {{varName}} for constants or runtime variables. "
            "Example: {'city': '{{city}}'}."
        ),
    )
    body: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Request body as a flat or nested JSON object. "
            "Use {{varName}} string placeholders for dynamic values. "
            "Example: {'userId': '{{userId}}', 'vehicleId': '{{vehicleId}}', "
            "'startDate': '{{startDate}}'}."
        ),
    )

    expected_status_code: int = Field(
        description="Expected HTTP response status code for a successful call, e.g. 200, 201, 204."
    )

    extract_vars: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Variables to extract from the response body for use in later steps. "
            "Key = variable name, value = JSONPath expression. "
            "Example: {'bookingId': '$.bookingId', 'vehicleId': '$.items[0].vehicleId'}."
        ),
    )
    assertions: List[Assertion] = Field(
        default_factory=list,
        description="Assertions to validate the response of this step.",
    )


# ---------------------------------------------------------------------------
# Top-level output of Agent 1
# ---------------------------------------------------------------------------


class ScenarioStabilizationInput(BaseModel):
    """
    Complete test scenario output produced by Agent 1.

    Passed directly to Agent 2 as its execution plan.
    """

    scenario_name: str = Field(description="Name / identifier of the test scenario.")
    description: str = Field(description="One-sentence summary of what the scenario tests.")
    steps: List[TestStep] = Field(description="Ordered list of test steps to execute.")
