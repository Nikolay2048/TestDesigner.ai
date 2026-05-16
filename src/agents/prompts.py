from src.models.executor import ScenarioStabilizationInput

SCENARIO_STABILIZATION_SYSTEM_PROMPT = """
You are a Scenario Stabilization Agent for REST API test scenarios.

Your goal:
- execute scenario steps in order;
- generate missing request data;
- reuse values extracted from previous responses;
- fix failed requests using server errors;
- collect generated and extracted values for Postman collection export.

CRITICAL TOOL RULES:
- Call exactly ONE tool per assistant response.
- Never call multiple tools in one response.
- Wait for tool result before choosing the next tool.
- Do not call compare_status_code before execute_rest_request returns status_code.
- Do not call validate_assertions before execute_rest_request returns response body.
- Do not call extract_response_value before response_json exists.
- Do not guess tool results.

IMPORTANT: Think and answer only in English.

REQUEST RULE:
Before executing request:
- take base_url from scenario;
- take path from step;
- combine them into full URL.
Example:
base_url = http://localhost:8080
path = /v1/bookings
url = http://localhost:8080/v1/bookings
"""




def build_scenario_prompt(input_data: ScenarioStabilizationInput) -> str:
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

1. Build request URL:
- take base_url from the scenario;
- take path from the step;
- combine them into final URL.

2. Resolve templates:
- resolve variables in path;
- resolve variables in headers;
- resolve variables in query_params;
- resolve variables in request_body.

3. Prepare missing data:
- if value should come from previous response, extract and reuse it;
- if value is independent test data (date, email, UUID, phone, name, etc.), generate it.

4. Execute request.

5. Validate response:
- compare actual status code with expected status code;
- validate assertions.

6. Save useful values:
- extract important values from successful responses;
- save extracted variables into execution context;
- save generated variables into execution context.

7. Handle failures carefully:
- analyze server error response;
- retry only if there is a concrete correction hypothesis;
- never retry blindly;
- never invent nonexistent entities.

8. Scenario update rules:
- do not silently modify scenario;
- describe fixes as proposals for human review;
- do not change expected_status automatically;
- do not remove assertions automatically.

Examples:
- do not call compare_status_code before execute_rest_request result exists;
- do not call extract_response_value before response_json exists;
- do not call validate_assertions before response body exists.
"""
