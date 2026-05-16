from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from .models import GeneratedValueResult


DATA_GENERATION_AGENT_PROMPT = """
You are a Test Data Generation Agent.

Generate one valid API test value.

Rules:
- Return ONLY JSON.
- No markdown.
- No explanations outside JSON.
- Value must match schema type and format.
- Generate realistic values.
- Prefer unique values for emails and usernames.
- Prefer future dates for bookings/reservations.
- Do not generate IDs of existing business entities if they should come from previous API responses.

JSON format:
{
  "value": "...",
  "source_type": "generated",
  "generator_name": "...",
  "generator_params": {},
  "reason": "..."
}
"""


class DataGenerationAgent:
    """Small LLM agent responsible only for independent test data generation."""

    def __init__(self, llm):
        self.llm = llm

    def generate(
        self,
        field_name: str,
        field_schema: Dict[str, Any],
        business_context: str,
        generation_goal: str,
    ) -> GeneratedValueResult:
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

        response = self.llm.invoke([
            SystemMessage(content=DATA_GENERATION_AGENT_PROMPT),
            HumanMessage(content=user_prompt),
        ])

        content = str(response.content).strip()
        try:
            parsed = json.loads(content)
            return GeneratedValueResult.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError):
            # Deterministic fallback keeps pipeline alive and makes the failure visible.
            fallback = self._fallback_value(field_name, field_schema, generation_goal)
            return GeneratedValueResult(
                value=fallback["value"],
                generator_name=fallback["generator_name"],
                generator_params=fallback["generator_params"],
                reason=f"Fallback generation because LLM returned invalid JSON. Goal: {generation_goal}",
            )

    @staticmethod
    def _fallback_value(field_name: str, field_schema: Dict[str, Any], generation_goal: str) -> Dict[str, Any]:
        lower = field_name.lower()
        field_format = field_schema.get("format")
        field_type = field_schema.get("type")

        if field_format == "date-time" or "date" in lower:
            value = (datetime.now() + timedelta(hours=1)).replace(microsecond=0).isoformat()
            return {"value": value, "generator_name": "fallback_datetime", "generator_params": {"strategy": "now_plus_hours", "hours": 1}}

        if "email" in lower:
            return {"value": f"test.{int(datetime.now().timestamp())}@example.com", "generator_name": "fallback_email", "generator_params": {}}

        if field_type in ["integer", "number"]:
            return {"value": field_schema.get("minimum", 1), "generator_name": "fallback_number", "generator_params": {}}

        return {"value": f"test-{int(datetime.now().timestamp())}", "generator_name": "fallback_string", "generator_params": {}}
