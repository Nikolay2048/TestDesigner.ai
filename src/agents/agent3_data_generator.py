"""Agent 3: data generation agent.

Agent 3 owns data-generation policy. Agent 2 asks it for a variable, business
context, goal, and constraints. Agent 3 first uses the reviewed generator
registry. If no suitable function exists, it asks the LLM to create a new
single-expression generator and persists that policy for future runs.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.agents.generator_registry import GeneratorFunction, GeneratorRegistry
from src.models.execution import GeneratedVariable, ToolCallRecord, VariableContext
from src.models.scenario import BusinessRule, TestStep, VariableSource
from src.utils.config import AppConfig

logger = logging.getLogger(__name__)

_DEFAULT_REGISTRY_PATH = Path("data/generator_registry.json")


class DataGeneratorAgent:
    """Generates runtime data values for Agent 2."""

    name = "agent3"

    def __init__(self, config: AppConfig, registry_path: Path = _DEFAULT_REGISTRY_PATH) -> None:
        self.config = config
        self._registry = GeneratorRegistry(registry_path)
        self._llm: Any = None

    @property
    def registry(self) -> GeneratorRegistry:
        return self._registry

    def generate_variable(
        self,
        variable_name: str,
        step: TestStep,
        context: VariableContext,
        business_context: str,
        business_rules: list[BusinessRule],
        source: Optional[VariableSource] = None,
        previous_error: Optional[str] = None,
    ) -> tuple[Optional[GeneratedVariable], ToolCallRecord]:
        """Generate one variable and return the value plus audit record."""

        values = context.values()
        rules = [
            rule.description
            for rule in business_rules
            if variable_name in rule.applies_to_variables or step.step in rule.applies_to_steps
        ]
        requires: Dict[str, Any] = {}
        if source:
            requires.update(source.generation_requires)
        if rules:
            requires["business_rules"] = rules
        if previous_error:
            requires["previous_error"] = previous_error

        logger.info(
            "Agent3: generate variable=%s step=%s requires=%s",
            variable_name,
            step.step,
            requires,
        )

        func = self._registry.lookup(variable_name)
        if func is None or previous_error:
            func = self._create_or_reuse_policy(variable_name, step, business_context, requires, func)

        if func is None:
            return None, ToolCallRecord(
                agent=self.name,
                tool="generator_registry",
                action="generate_variable",
                input_summary={"variable": variable_name, "requires": requires},
                success=False,
                error="No generator policy available",
            )

        value = self._registry.execute(func, values)
        if value is None:
            return None, ToolCallRecord(
                agent=self.name,
                tool="generator_registry",
                action="execute_generator",
                input_summary={"variable": variable_name, "function": func.name},
                success=False,
                error="Generator returned None",
            )

        generated = GeneratedVariable(
            name=variable_name,
            generated_value=value,
            generator_name=func.name,
            generator_params=requires,
            reason=source.generation_goal if source else f"Required by step {step.step}.",
            overwrite_reason="Regenerated after failed attempt." if previous_error else None,
            generator_function=func.python_expr,
        )
        return generated, ToolCallRecord(
            agent=self.name,
            tool="generator_registry",
            action="execute_generator",
            input_summary={"variable": variable_name, "function": func.name, "requires": requires},
            output_summary={"value": value},
        )

    def _create_or_reuse_policy(
        self,
        variable_name: str,
        step: TestStep,
        business_context: str,
        requires: Dict[str, Any],
        existing: Optional[GeneratorFunction],
    ) -> Optional[GeneratorFunction]:
        if existing is not None and not requires.get("previous_error"):
            return existing
        if existing is not None and not self._should_rewrite_policy(requires):
            return existing
        return self._llm_create_function(variable_name, step, business_context, requires) or existing

    @staticmethod
    def _should_rewrite_policy(requires: Dict[str, Any]) -> bool:
        error = str(requires.get("previous_error") or "").lower()
        return any(token in error for token in ("date", "format", "invalid", "validation", "400"))

    def _llm_create_function(
        self,
        variable_name: str,
        step: TestStep,
        business_context: str,
        requires: Dict[str, Any],
    ) -> Optional[GeneratorFunction]:
        if self._llm is None:
            from src.utils.llm_factory import create_llm

            self._llm = create_llm(self.config)

        prompt = (
            "Create a deterministic Python data generator for API testing.\n"
            "Return only JSON with fields description, python_expr, js_snippet.\n"
            "The python_expr must be a single expression. Available names: "
            "datetime, timedelta, timezone, random, string, uuid, context, str, int, float, bool, round.\n\n"
            f"Variable: {variable_name}\n"
            f"Step: {step.method} {step.path}\n"
            f"Step body template: {step.request_body}\n"
            f"Business context: {business_context[:3000]}\n"
            f"Requires: {json.dumps(requires, ensure_ascii=False)}\n"
            "The js_snippet must set the same variable with pm.collectionVariables.set()."
        )
        try:
            response = self._llm.invoke(prompt)
            content = getattr(response, "content", response)
            if not isinstance(content, str):
                content = str(content)
            content = re.sub(r"^```(?:json)?\s*", "", content.strip())
            content = re.sub(r"\s*```$", "", content)
            data = json.loads(content)
            from datetime import datetime as _dt

            func = GeneratorFunction(
                name=variable_name,
                description=data["description"],
                python_expr=data["python_expr"],
                js_snippet=data["js_snippet"],
                source="llm_generated",
                created_at=_dt.now(tz=timezone.utc).isoformat(),
            )
            self._registry.register(func)
            logger.info("Agent3: saved generated policy for %s", variable_name)
            return func
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent3: failed to create generator for %s: %s", variable_name, exc)
            return None
