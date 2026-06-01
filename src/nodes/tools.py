import re
from datetime import date
from typing import Literal, Optional, Dict, Any

import requests
from langchain_core.tools import tool

from utils.utils import log_tool_execution


@tool
@log_tool_execution
def execute_rest_request(
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
        url: str,
        headers: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        request_body: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 15,
) -> Dict[str, Any]:
    """
Execute REST API request.

This tool automatically resolves all {{ variables }} using execution context before sending request.
Do not manually replace {{ variables }} before calling this tool.

You may pass templates in:
    - url
    - headers
    - query_params
    - request_body

This tool only executes request.
Do not validate response here.

 Example request body:
    {
      "vehicleId": "{{ availableVehicleId }}",
      "startDate": "{{ startDate }}"
    }

Return:
- status_code
- response_json
- response_text
- response_headers
- error
"""

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=query_params,
            json=request_body,
            timeout=timeout_seconds,
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


# @tool
# @log_tool_execution
# def extract_response_value(response_json: Any, extraction_expression: str) -> Dict[str, Any]:
#     """
#     Extract value from API response JSON.
#
#     Use expression to find value.
#
#     Example:
#     items[0].vehicleId
#
#     Expected result:
#     "vehicle-1"
#
#     Use extracted values in next scenario steps.
#
#     If value does not exist:
#     - return error
#     - do not invent value
#     """
#
#     value = _extract_value(response_json, extraction_expression)
#
#     return {
#         "value": value,
#         "extraction_expression": extraction_expression,
#     }
#
#
# @tool
# @log_tool_execution
# def save_extracted_variable_to_context(
#         context: Dict[str, Any],
#         variable_name: str,
#         extracted_value: Any,
#         source_step: int,
#         extraction_expression: str,
# ) -> Dict[str, Any]:
#     """
#     Save variable into execution context.
#
#     Saved variables can be reused later.
#
#     Example:
#     availableVehicleId = "vehicle-1"
#
#     Then next step can use:
#     {{ availableVehicleId }}
#
#     Always save:
#     - variable name
#     - value
#     - source step
#     - extraction expression
#     """
#
#     context_model = ExecutionContext.model_validate(context)
#
#     context_model.variables[variable_name] = ExtractedVariable(
#         extracted_value=extracted_value,
#         source_step=source_step,
#         extraction_expression=extraction_expression,
#     )
#
#     return context_model.model_dump()
#
#
# @tool
# @log_tool_execution
# def save_generated_variable_to_context(
#         context: Dict[str, Any],
#         variable_name: str,
#         generated_value: Any,
#         generator_name: str,
#         generator_params: Optional[Dict[str, Any]] = None,
# ) -> Dict[str, Any]:
#     """
#     Save variable into execution context.
#
#     Saved variables can be reused later.
#
#     Example:
#     availableVehicleId = "vehicle-1"
#
#     Then next step can use:
#     {{ availableVehicleId }}
#
#     Always save:
#     - variable name
#     - value
#     - source step
#     - extraction expression
#     """
#
#     context_model = ExecutionContext.model_validate(context)
#
#     context_model.generated_variables[variable_name] = GeneratedVariable(
#         generated_value=generated_value,
#         generator_name=generator_name,
#         generator_params=generator_params or {},
#     )
#
#     return context_model.model_dump()
#
#
# @tool
# @log_tool_execution
# def compare_status_code(expected_status: int, actual_status: Optional[int]) -> Dict[str, Any]:
#     """
#     Compare actual HTTP status code with expected status code.
#
#     Example:
#     expected = 201
#     actual = 404
#
#     Rules:
#     - comparison must be exact
#     - 404 is not equal to 201
#     - do not ignore mismatch
#
#     Return:
#     - passed true/false
#     - expected status
#     - actual status
#     - explanation
#     """
#
#     passed = expected_status == actual_status
#
#     return {
#         "passed": passed,
#         "expected_status": expected_status,
#         "actual_status": actual_status,
#         "message": "Status code matched." if passed else f"Expected {expected_status}, got {actual_status}.",
#     }
#
#
# def _parse_literal(raw_value: str) -> Any:
#     raw_value = raw_value.strip()
#
#     if raw_value == "today()":
#         return date.today().isoformat()
#
#     if raw_value.startswith("'") and raw_value.endswith("'"):
#         return raw_value[1:-1]
#
#     if raw_value.startswith('"') and raw_value.endswith('"'):
#         return raw_value[1:-1]
#
#     if raw_value.isdigit():
#         return int(raw_value)
#
#     try:
#         return float(raw_value)
#     except ValueError:
#         return raw_value
#
#
# @tool
# @log_tool_execution
# def validate_assertions(assertions: List[str], response_json: Any) -> Dict[str, Any]:
#     """Validate simple assertions against response JSON. Supports ==, !=, >, >=, <, <= for response paths."""
#
#     checked = []
#     failed = []
#     skipped = []
#
#     operators = ["==", "!=", ">=", "<=", ">", "<"]
#
#     for assertion in assertions:
#         operator = next((op for op in operators if op in assertion), None)
#
#         if operator is None:
#             skipped.append({"assertion": assertion, "reason": "Unsupported assertion format."})
#             continue
#
#         left_raw, right_raw = assertion.split(operator, 1)
#         left_raw = left_raw.strip()
#         right_raw = right_raw.strip()
#
#         try:
#             left_value = _extract_value(response_json, left_raw)
#             right_value = _parse_literal(right_raw)
#
#             if operator == "==":
#                 passed = left_value == right_value
#             elif operator == "!=":
#                 passed = left_value != right_value
#             elif operator == ">":
#                 passed = left_value > right_value
#             elif operator == ">=":
#                 passed = left_value >= right_value
#             elif operator == "<":
#                 passed = left_value < right_value
#             elif operator == "<=":
#                 passed = left_value <= right_value
#             else:
#                 passed = False
#
#             result = {
#                 "assertion": assertion,
#                 "passed": passed,
#                 "actual": left_value,
#                 "expected": right_value,
#                 "operator": operator,
#             }
#
#             checked.append(result)
#
#             if not passed:
#                 failed.append(result)
#
#         except Exception as exc:
#             failed.append({
#                 "assertion": assertion,
#                 "passed": False,
#                 "error": str(exc),
#             })
#
#     return {
#         "passed": len(failed) == 0,
#         "checked": checked,
#         "failed": failed,
#         "skipped": skipped,
#     }


SCENARIO_STABILIZATION_TOOLS = [
    execute_rest_request,
    extract_response_value,
    # save_extracted_variable_to_context,
    # save_generated_variable_to_context,
    # compare_status_code,
    # validate_assertions,
    # ask_data_generation_agent,
]
