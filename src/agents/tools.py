import re
from datetime import date

import requests
from langchain_core.tools import tool

from src.agents.data_generation_agent import ask_data_generation_agent
from src.agents.utils import log_tool_execution
from src.models.executor import *


def _get_context_value(context: ExecutionContext, variable_name: str) -> Any:
    if variable_name in context.variables:
        return context.variables[variable_name].extracted_value

    if variable_name in context.generated_variables:
        return context.generated_variables[variable_name].generated_value

    raise ValueError(f"Variable '{variable_name}' not found in execution context.")


def _extract_value(data: Any, expression: str) -> Any:
    expression = expression.strip()

    if expression.startswith("$."):
        expression = expression[2:]

    elif expression.startswith("response."):
        expression = expression.replace("response.", "", 1)

    current = data

    for part in expression.split("."):
        array_match = re.fullmatch(r"(\w+)\[(\d+)]", part)

        if array_match:
            key = array_match.group(1)
            index = int(array_match.group(2))
            current = current[key][index]
        else:
            current = current[part]

    return current


def _resolve_templates(data: Any, context: Dict[str, Any]) -> Any:
    """
    Resolve all {{ variables }} using execution context.
    """

    context_model = ExecutionContext.model_validate(context)
    pattern = r"\{\{\s*(\w+)\s*\}\}"

    def resolve(value: Any) -> Any:
        if isinstance(value, str):
            matches = list(re.finditer(pattern, value))

            if not matches:
                return value

            if len(matches) == 1 and matches[0].group(0) == value:
                variable_name = matches[0].group(1)
                return _get_context_value(context_model, variable_name)

            def replace(match: re.Match) -> str:
                variable_name = match.group(1)
                return str(_get_context_value(context_model, variable_name))

            return re.sub(pattern, replace, value)

        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}

        if isinstance(value, list):
            return [resolve(item) for item in value]

        return value

    return resolve(data)


@tool
@log_tool_execution
def execute_rest_request(
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
        url: str,
        headers: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        request_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
Execute REST API request.

Args:
    method: HTTP method (GET, POST, PUT, PATCH, DELETE).
    url: Full target request URL.
    headers: Optional HTTP headers.
    query_params: Optional query string parameters.
    request_body: Optional JSON request body.

The tool sends a single HTTP request and returns:
- HTTP status code
- Parsed JSON response if available
- Raw response text for non-JSON responses
- Response headers
- Request execution error if request failed

Rules:
- Use GET requests without request_body unless API explicitly supports it.
- Use request_body only for POST, PUT, or PATCH requests.
- Always analyze returned status_code before continuing scenario execution.
- If response_json is not null, use it for assertions and variable extraction.
- If error is not null, request execution failed before receiving response.

Returns:
{
    "status_code": int | None,
    "response_json": dict | list | None,
    "response_text": str | None,
    "response_headers": dict,
    "error": str | None
}
"""
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=query_params,
            json=request_body,
        )

        try:
            response_json = response.json()
            response_text = None
        except ValueError:
            response_json = None
            response_text = response.text

        return {
            "status_code": response.status_code,
            "response_json": response_json,
            "response_text": response_text,
            "response_headers": dict(response.headers),
            "error": None,
        }
    except requests.RequestException as exc:
        return {
            "status_code": None,
            "response_json": None,
            "response_text": None,
            "response_headers": {},
            "error": str(exc),
        }


@tool
@log_tool_execution
def extract_response_value(response_json: Any, extraction_expression: str) -> Dict[str, Any]:
    """
    Extract value from API response JSON.

    Use expression to find value.

    Example:
    items[0].vehicleId

    Use extracted values in next scenario steps.

    If value does not exist:
    - return error
    - do not invent value
    """

    value = _extract_value(response_json, extraction_expression)

    return {
        "value": value,
        "extraction_expression": extraction_expression,
    }


@tool
@log_tool_execution
def save_extracted_variable_to_context(
        context: Dict[str, Any],
        variable_name: str,
        extracted_value: Any,
        source_step: int,
        extraction_expression: str,
) -> Dict[str, Any]:
    """
    Save variable into execution context.

    Saved variables can be reused later.

    Example:
    availableVehicleId = "vehicle-1"

    Then next step can use:
    {{ availableVehicleId }}

    Always save:
    - variable name
    - value
    - source step
    - extraction expression
    """

    context_model = ExecutionContext.model_validate(context)

    context_model.variables[variable_name] = ExtractedVariable(
        extracted_value=extracted_value,
        source_step=source_step,
        extraction_expression=extraction_expression,
    )

    return context_model.model_dump()


@tool
@log_tool_execution
def save_generated_variable_to_context(
        context: Dict[str, Any],
        variable_name: str,
        generated_value: Any,
        generator_name: str,
        generator_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Save variable into execution context.

    Saved variables can be reused later.

    Example:
    availableVehicleId = "vehicle-1"

    Then next step can use:
    {{ availableVehicleId }}

    Always save:
    - variable name
    - value
    - source step
    - extraction expression
    """

    context_model = ExecutionContext.model_validate(context)

    context_model.generated_variables[variable_name] = GeneratedVariable(
        generated_value=generated_value,
        generator_name=generator_name,
        generator_params=generator_params or {},
    )

    return context_model.model_dump()


@tool
@log_tool_execution
def compare_status_code(expected_status: int, actual_status: Optional[int]) -> Dict[str, Any]:
    """
    Compare actual HTTP status code with expected status code.

    Example:
    expected = 201
    actual = 404

    Rules:
    - comparison must be exact
    - 404 is not equal to 201
    - do not ignore mismatch

    Return:
    - passed true/false
    - expected status
    - actual status
    - explanation
    """

    passed = expected_status == actual_status

    return {
        "passed": passed,
        "expected_status": expected_status,
        "actual_status": actual_status,
        "message": "Status code matched." if passed else f"Expected {expected_status}, got {actual_status}.",
    }


def _parse_literal(raw_value: str) -> Any:
    raw_value = raw_value.strip()

    if raw_value == "today()":
        return date.today().isoformat()

    if raw_value.startswith("'") and raw_value.endswith("'"):
        return raw_value[1:-1]

    if raw_value.startswith('"') and raw_value.endswith('"'):
        return raw_value[1:-1]

    if raw_value.isdigit():
        return int(raw_value)

    try:
        return float(raw_value)
    except ValueError:
        return raw_value


@tool
@log_tool_execution
def validate_assertions(assertions: List[str], response_json: Any) -> Dict[str, Any]:
    """Validate simple assertions against response JSON. Supports ==, !=, >, >=, <, <= for response paths."""

    checked = []
    failed = []
    skipped = []

    operators = ["==", "!=", ">=", "<=", ">", "<"]

    for assertion in assertions:
        operator = next((op for op in operators if op in assertion), None)

        if operator is None:
            skipped.append({"assertion": assertion, "reason": "Unsupported assertion format."})
            continue

        left_raw, right_raw = assertion.split(operator, 1)
        left_raw = left_raw.strip()
        right_raw = right_raw.strip()

        try:
            left_value = _extract_value(response_json, left_raw)
            right_value = _parse_literal(right_raw)

            if operator == "==":
                passed = left_value == right_value
            elif operator == "!=":
                passed = left_value != right_value
            elif operator == ">":
                passed = left_value > right_value
            elif operator == ">=":
                passed = left_value >= right_value
            elif operator == "<":
                passed = left_value < right_value
            elif operator == "<=":
                passed = left_value <= right_value
            else:
                passed = False

            result = {
                "assertion": assertion,
                "passed": passed,
                "actual": left_value,
                "expected": right_value,
                "operator": operator,
            }

            checked.append(result)

            if not passed:
                failed.append(result)

        except Exception as exc:
            failed.append({
                "assertion": assertion,
                "passed": False,
                "error": str(exc),
            })

    return {
        "passed": len(failed) == 0,
        "checked": checked,
        "failed": failed,
        "skipped": skipped,
    }


# @tool
# @log_tool_execution
# def ask_data_generation_agent(
#         field_name: str,
#         field_schema: Dict[str, Any],
#         business_context: str,
#         generation_goal: str,
# ) -> Dict[str, Any]:
#     """
# Generate valid test data.
#
# Use only when required value does not exist yet.
#
# Examples:
# - future datetime
# - email
# - UUID
# - username
# - phone number
#
# Generated data must:
# - be realistic
# - match expected format
# - be valid for API
# """
#
#     field_lower = field_name.lower()
#     field_type = field_schema.get("type")
#     field_format = field_schema.get("format")
#
#     if "email" in field_lower:
#         value = f"test.{uuid.uuid4().hex[:8]}@example.com"
#         generator_name = "generate_email"
#         generator_params = {"strategy": "unique_email"}
#
#     elif "firstname" in field_lower or "first_name" in field_lower:
#         value = "Ivan"
#         generator_name = "generate_string"
#         generator_params = {"strategy": "first_name"}
#
#     elif "lastname" in field_lower or "last_name" in field_lower:
#         value = "Petrov"
#         generator_name = "generate_string"
#         generator_params = {"strategy": "last_name"}
#
#     elif "uuid" in field_lower or field_format == "uuid":
#         value = str(uuid.uuid4())
#         generator_name = "generate_uuid"
#         generator_params = {"strategy": "random_uuid"}
#
#     elif "birth" in field_lower or "age" in business_context.lower():
#         value = (date.today() - timedelta(days=365 * 25)).isoformat()
#         generator_name = "generate_date"
#         generator_params = {"strategy": "adult_birth_date", "age_years": 25}
#
#     elif field_format == "date":
#         value = (date.today() + timedelta(days=1)).isoformat()
#         generator_name = "generate_date"
#         generator_params = {"strategy": "today_plus_days", "days": 1}
#
#     elif field_format == "date-time":
#         value = (datetime.now() + timedelta(hours=1)).replace(microsecond=0).isoformat()
#         generator_name = "generate_datetime"
#         generator_params = {"strategy": "now_plus_hours", "hours": 1}
#
#     elif field_type in ["integer", "number"]:
#         minimum = field_schema.get("minimum", 1)
#         maximum = field_schema.get("maximum", 100)
#         value = random.randint(int(minimum), int(maximum))
#         generator_name = "generate_number"
#         generator_params = {"minimum": minimum, "maximum": maximum}
#
#     elif "enum" in field_schema:
#         value = field_schema["enum"][0]
#         generator_name = "generate_enum"
#         generator_params = {"strategy": "first_enum_value"}
#
#     else:
#         value = f"test-{uuid.uuid4().hex[:8]}"
#         generator_name = "generate_string"
#         generator_params = {"strategy": "unique_plain_string"}
#
#     return {
#         "value": value,
#         "source_type": "generated",
#         "generator_name": generator_name,
#         "generator_params": generator_params,
#         "reason": f"Generated value for field '{field_name}'. Goal: {generation_goal}",
#     }


EXECUTOR_TOOLS = [
    execute_rest_request,
    extract_response_value
]

SCENARIO_STABILIZATION_TOOLS = [
    # resolve_templates,
    execute_rest_request,
    extract_response_value,
    save_extracted_variable_to_context,
    save_generated_variable_to_context,
    compare_status_code,
    validate_assertions,
    ask_data_generation_agent,
]
