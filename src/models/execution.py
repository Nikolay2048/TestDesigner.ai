"""
Execution result models — output of Agent 2.

These models capture the full trace of a scenario run: every HTTP request,
response, extracted variable, and assertion result.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class AssertionResult(BaseModel):
    """Result of evaluating a single :class:`~src.models.scenario.Assertion`."""

    description: str
    path: str
    operator: str
    expected: Optional[Union[str, int, float, bool]] = None
    actual: Optional[Any] = None
    passed: bool
    error: Optional[str] = None


class StepResult(BaseModel):
    """Full execution trace for one :class:`~src.models.scenario.TestStep`."""

    step_num: int
    name: str
    status: Literal["passed", "failed", "skipped"]
    attempts: int = Field(default=1, description="How many times this step was attempted (incl. retries).")

    # Request actually sent
    method: str
    url: str
    request_params: Optional[Dict[str, str]] = None
    request_body: Optional[Any] = None

    # Response received
    response_status: Optional[int] = None
    response_body: Optional[Any] = None

    # Outcomes
    extracted_vars: Dict[str, Any] = Field(default_factory=dict)
    assertion_results: List[AssertionResult] = Field(default_factory=list)
    error: Optional[str] = None


class ScenarioExecutionResult(BaseModel):
    """Aggregated result of running a full :class:`~src.models.scenario.ScenarioStabilizationInput`."""

    scenario_name: str
    overall_status: Literal["passed", "failed"]
    total_steps: int
    passed_steps: int
    failed_steps: int
    steps: List[StepResult]
    final_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Variable context after all steps have run.",
    )
