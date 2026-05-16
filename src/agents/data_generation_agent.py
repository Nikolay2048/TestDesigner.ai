from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage

import json
from typing import Any, Dict

from src.agents.llm_provider import llm
from src.agents.utils import log_tool_execution

DATA_GENERATION_AGENT_PROMPT = """
You are a Test Data Generation Agent.

Generate one valid API test value.

Rules:
- Return ONLY JSON.
- No markdown.
- No explanations.
- Value must match schema type and format.
- Generate realistic values.
- Prefer unique values for emails/usernames.
- Prefer future dates for bookings/reservations.
- Do not invent existing entity IDs.

JSON format:
{
  "value": "...",
  "source_type": "generated",
  "generator_name": "...",
  "generator_params": {},
  "reason": "..."
}
"""


@tool
@log_tool_execution
def ask_data_generation_agent(
    field_name: str,
    field_schema: Dict[str, Any],
    business_context: str,
    generation_goal: str,
) -> Dict[str, Any]:
    """
    Generate valid test data for API request fields.

    Use when required value:
    - does not exist in execution context;
    - cannot be extracted from previous responses.

    Examples:
    - future datetime
    - email
    - UUID
    - username
    - phone number
    """

    #
    # Internal lightweight LLM
    #


    user_prompt = f"""
Generate test data for API field.

Field name:
{field_name}

Field schema:
{json.dumps(field_schema, ensure_ascii=False, indent=2)}

Business context:
{business_context}

Generation goal:
{generation_goal}

Return ONLY JSON.
"""

    response = llm.invoke([
        SystemMessage(content=DATA_GENERATION_AGENT_PROMPT),
        HumanMessage(content=user_prompt),
    ])

    content = response.content.strip()

    try:
        parsed = json.loads(content)

        return {
            "value": parsed.get("value"),
            "source_type": parsed.get("source_type", "generated"),
            "generator_name": parsed.get(
                "generator_name",
                "llm_generate_value",
            ),
            "generator_params": parsed.get(
                "generator_params",
                {},
            ),
            "reason": parsed.get(
                "reason",
                f"Generated value for field '{field_name}'. Goal: {generation_goal}",
            ),
        }

    except Exception:
        #
        # fallback if model returned invalid JSON
        #
        return {
            "value": content,
            "source_type": "generated",
            "generator_name": "llm_generate_value",
            "generator_params": {
                "field_name": field_name,
            },
            "reason": (
                f"LLM generated raw value for field '{field_name}'. "
                f"Goal: {generation_goal}"
            ),
        }