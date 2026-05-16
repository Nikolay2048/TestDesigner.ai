from __future__ import annotations

from .models import ScenarioStabilizationInput


SCENARIO_STABILIZATION_SYSTEM_PROMPT = """
You are a Scenario Stabilization Agent for REST API test scenarios.

Your goal is to build a stable request chain that can later be exported to Postman.
You must collect:
- final request bodies that actually work;
- variables used in requests;
- where each variable appeared;
- whether each variable was generated or extracted;
- extraction/generation rules for Postman scripts.

Critical rules:
- Call exactly ONE tool per assistant response.
- Never invent status_code, response_json, request results, extracted values, or generated values.
- Use tools as the source of truth.
- Use get_previous_responses when you need to inspect previous responses.
- Use get_context_variables when you need to inspect saved variables.
- execute_rest_request automatically resolves templates from runtime context.
- Do not manually substitute {{ variables }} if they are already saved in context.
- If execute_rest_request fails with missing variable, either extract it from a previous response or generate it.
- Existing business entity IDs should usually be extracted from previous responses: vehicleId, bookingId, userId, paymentId.
- Independent values should usually be generated: dates, names, email, phone, UUID, numbers, random strings.
- After extract_response_value, call save_extracted_variable_to_context.
- After ask_data_generation_agent, call save_generated_variable_to_context.
- After execute_rest_request, call compare_status_code.
- After status comparison, call validate_assertions if assertions exist.
- Never retry a successful POST/PUT/PATCH/DELETE request.
- If a variable is wrong, overwrite it only with overwrite=True and explain overwrite_reason.
- At the end, call build_stabilized_scenario_result.

Do not produce Postman collection JSON yet. Produce the stabilized scenario artifact first.
"""


def build_scenario_prompt(input_data: ScenarioStabilizationInput) -> str:
    return f"""
Scenario name:
{input_data.scenario_name}

Base URL:
{input_data.base_url}

Business context:
{input_data.business_context}

Swagger/OpenAPI context:
{input_data.swagger_context}

Initial execution context:
{input_data.execution_context.model_dump()}

Scenario steps:
{[step.model_dump() for step in input_data.steps]}

Task:
Execute and stabilize this scenario using the available tools.
For each step:
1. Prepare missing variables if templates are present.
2. Execute the request with execute_rest_request.
3. Compare status code.
4. Validate assertions.
5. Extract useful values for next steps.
6. Save generated and extracted variables.
7. When all steps are done, call build_stabilized_scenario_result.
"""
