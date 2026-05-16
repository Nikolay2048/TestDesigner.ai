from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from langchain.agents import create_agent

import os

from langchain_ollama import ChatOllama

from src.agents.tools import SCENARIO_STABILIZATION_TOOLS
from src.agents.utils import check_llm_connection
from src.models.executor import ExecutionContext, TestStep


class ScenarioStabilizationInput(BaseModel):
    scenario_name: str = Field(description="Human-readable scenario name.")
    base_url: str = Field(description="Base API URL, for example http://localhost:8080.")
    steps: List[TestStep] = Field(description="Scenario steps from knowledge base.")
    execution_context: ExecutionContext = Field(default_factory=ExecutionContext,
                                                description="Current execution context.")


SCENARIO_STABILIZATION_AGENT_PROMPT = """
You are a Scenario Stabilization Agent for REST API test scenarios.

Your goal:
- execute scenario steps;
- resolve variables needed for requests;
- extract variables from previous responses;
- ask data generation agent for independent generated values;
- retry failed steps only to verify a correction hypothesis;
- produce clear correction proposals for human review.

Rules:
1. Execute steps in order.
2. Do not change expected_status.
3. Do not change assertions automatically.
4. If a value is an existing entity id, prefer extracting it from previous responses.
5. If a value is independent test data such as name, date, email, number, phone, or UUID, use ask_data_generation_agent.
6. If you extract a value from response, call save_extracted_variable_to_context.
7. If you generate a value, call save_generated_variable_to_context.
8. Before execute_rest_request, call resolve_templates for path, headers, query_params, and request_body if they contain templates.
9. After execute_rest_request, call compare_status_code.
10. Call validate_assertions after status code matches or when response body must be checked.
11. If a correction makes the step pass, describe it as a proposal, not as an automatic change.
12. Do not hide failed attempts.
13. Return a concise summary of executed steps, failed steps, generated variables, extracted variables, and proposed scenario updates.

CRITICAL TOOL CALL RULE:
Call only one tool at a time.

"""


class ScenarioStabilizationAgent:
    def __init__(self, llm):
        self.agent = create_agent(
            model=llm,
            tools=SCENARIO_STABILIZATION_TOOLS,
            system_prompt=SCENARIO_STABILIZATION_AGENT_PROMPT,
        )

    def run(self, input_data: ScenarioStabilizationInput) -> Dict[str, Any]:
        user_task = self._build_user_task(input_data)

        result = self.agent.invoke({
            "messages": [
                {
                    "role": "user",
                    "content": user_task,
                }
            ]
        })

        return result

    def _build_user_task(self, input_data: ScenarioStabilizationInput) -> str:
        return f"""
Scenario name:
{input_data.scenario_name}

Base URL:
{input_data.base_url}

Initial execution context:
{input_data.execution_context.model_dump()}

Scenario steps:
{input_data.steps}

Task:
Execute and stabilize this scenario.
For each step:
- build the request;
- resolve variables;
- execute request;
- compare status code;
- validate assertions;
- extract useful variables for next steps;
- generate independent values when needed;
- retry only when you have a concrete correction hypothesis;
- report all changes as proposals for human review.
"""
