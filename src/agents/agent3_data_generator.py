"""
Agent 3 — Data Generator.

Generates or refreshes runtime variable values that are either:

* **Missing** — referenced as ``{{varName}}`` in a step but not yet in context.
* **Stale / invalid** — a previous attempt with the current values failed; the
  agent produces fresh data for a retry.

How it works
------------
1. For each missing variable, look it up in the :class:`GeneratorRegistry`.
2. If found (builtin or previously saved LLM function), execute its
   ``python_expr`` and return the value.
3. If **not** found, call the LLM and ask it to produce:
   * A short description.
   * A single-line Python expression usable in the registry's eval context.
   * A JavaScript snippet for Postman pre-request scripts.
   The result is saved as a new :class:`GeneratorFunction` so future runs
   never call the LLM again for the same variable name.

Variable refresh (retry after failure)
---------------------------------------
``refresh()`` re-executes the generator for every ``{{var}}`` referenced by the
failed step that has a registered generator (i.e. variables that change over
time, like dates or random IDs).  Variables that are extracted from API
responses (``context`` kind) are *not* regenerated here — Agent 2 handles
those by re-running the extraction step.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

from src.agents.generator_registry import GeneratorFunction, GeneratorRegistry
from src.models.scenario import TestStep
from src.utils.config import AppConfig

logger = logging.getLogger(__name__)

_DEFAULT_REGISTRY_PATH = Path("data/generator_registry.json")

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


class DataGeneratorAgent:
    """
    Agent 3: generates missing or refreshed variable values for a test step.

    Args:
        config:        Application configuration (LLM settings for fallback).
        registry_path: Path to the JSON file where LLM-generated functions are
                       persisted.  Defaults to ``data/generator_registry.json``.
    """

    def __init__(
        self,
        config: AppConfig,
        registry_path: Path = _DEFAULT_REGISTRY_PATH,
    ) -> None:
        self.config = config
        self._registry = GeneratorRegistry(registry_path)
        self._llm: Any = None  # lazy-loaded only when needed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def registry(self) -> GeneratorRegistry:
        """Expose the registry (used by PostmanGenerator)."""
        return self._registry

    def generate(
        self,
        step: TestStep,
        context: Dict[str, Any],
        missing_vars: Set[str],
    ) -> Dict[str, Any]:
        """
        Produce values for *missing_vars* required by *step*.

        Args:
            step:         The test step that needs data.
            context:      Current variable context (already-resolved values).
            missing_vars: Variable names referenced in the step but absent
                          from *context*.

        Returns:
            ``{var_name: generated_value}`` — merge this into context before
            re-executing the step.
        """
        generated: Dict[str, Any] = {}

        for var in sorted(missing_vars):
            value = self._generate_one(var, step, context)
            if value is not None:
                generated[var] = value
                logger.info(
                    "Agent3 [%s]: %s = %r  (step %d)",
                    self._registry.lookup(var).source if self._registry.lookup(var) else "llm",
                    var, value, step.step_num,
                )
            else:
                logger.warning(
                    "Agent3: could not generate value for '%s'  (step %d)",
                    var, step.step_num,
                )

        return generated

    def refresh(
        self,
        step: TestStep,
        context: Dict[str, Any],
        error_description: str,
    ) -> Dict[str, Any]:
        """
        Regenerate dynamic variables when a step failed and must be retried.

        Re-executes the generator for every ``{{var}}`` in the step that has a
        registered generator — these are the vars whose values change each time
        (dates, random IDs, etc.).  Context vars extracted from API responses
        are not touched here.

        Args:
            step:              The failed step.
            context:           Current variable context.
            error_description: Human-readable failure reason (for logging).

        Returns:
            ``{var_name: fresh_value}`` — merge into context before retrying.
        """
        logger.info(
            "Agent3: refreshing data for step %d — %s",
            step.step_num, error_description,
        )

        all_refs = self._collect_var_refs(step)
        # Only refresh vars that have a registered generator (those are the ones
        # Agent 3 produced; context vars come from API responses, not from here).
        to_refresh = {v for v in all_refs if self._registry.lookup(v) is not None}

        if to_refresh:
            logger.debug("Agent3.refresh: re-generating %s", sorted(to_refresh))
        return self.generate(step, context, to_refresh)

    # ------------------------------------------------------------------
    # Internal generation logic
    # ------------------------------------------------------------------

    def _generate_one(
        self,
        var_name: str,
        step: TestStep,
        context: Dict[str, Any],
    ) -> Any:
        """Return a generated value for *var_name*, or ``None`` if all attempts fail."""

        # 1. Registry lookup (builtin or previously saved LLM function)
        logger.debug(
            "Agent3: looking up '%s' in registry (%d functions)",
            var_name, len(self._registry._functions),
        )
        func = self._registry.lookup(var_name)
        if func is not None:
            logger.debug(
                "Agent3: found '%s' [%s] — expr: %s",
                var_name, func.source, func.python_expr,
            )
            value = self._registry.execute(func, context)
            if value is not None:
                logger.debug("Agent3: eval('%s') → %r", func.name, value)
                return value
            logger.warning(
                "Agent3: registry function for '%s' returned None, trying LLM fallback",
                var_name,
            )
        else:
            logger.info("Agent3: '%s' not in registry — will call LLM", var_name)

        # 2. LLM fallback: create a new generator function and save it
        func = self._llm_create_function(var_name, step, context)
        if func is not None:
            self._registry.register(func)
            logger.info(
                "Agent3: 💾 new generator saved: '%s' — %s",
                func.name, func.description,
            )
            logger.debug("Agent3: python_expr = %s", func.python_expr)
            return self._registry.execute(func, context)

        return None

    # ------------------------------------------------------------------
    # LLM fallback — create a new GeneratorFunction
    # ------------------------------------------------------------------

    def _llm_create_function(
        self,
        var_name: str,
        step: TestStep,
        context: Dict[str, Any],
    ) -> Optional[GeneratorFunction]:
        """
        Ask the LLM to design a generator function for an unknown variable.

        The LLM returns a JSON object with ``description``, ``python_expr``,
        and ``js_snippet``.  The result is wrapped in a :class:`GeneratorFunction`
        and saved to the registry so future runs never need this call again.
        """
        if self._llm is None:
            from src.utils.llm_factory import create_llm
            self._llm = create_llm(self.config)

        logger.info("Agent3: calling LLM to create generator for '%s'", var_name)

        prompt = (
            f"You are a test data generator. Create a data generator for the variable '{var_name}'.\n\n"
            f"Context:\n"
            f"  Step description : {step.description}\n"
            f"  API call         : {step.method} {step.path}\n"
            f"  Context keys     : {sorted(context.keys())}\n\n"
            f"Return ONLY a JSON object with these exact fields (no markdown, no explanation):\n"
            f"{{\n"
            f'  "description": "short description of what this generates",\n'
            f'  "python_expr": "single-line Python expression — no imports, no def, no semicolons",\n'
            f'  "js_snippet":  "JavaScript block ending with pm.collectionVariables.set(\'{var_name}\', value)"\n'
            f"}}\n\n"
            f"Python eval context provides: datetime, timedelta, timezone, random, string, uuid, context.\n"
            f"Examples of valid python_expr:\n"
            f"  - str(random.randint(1, 100))\n"
            f"  - 'prefix_' + uuid.uuid4().hex[:8]\n"
            f"  - (datetime.now(tz=timezone.utc) + timedelta(days=7)).strftime('%Y-%m-%d')\n\n"
            f"For js_snippet: use pm.collectionVariables.set() and pm.variables.replaceIn() for built-in Postman vars."
        )

        logger.debug("Agent3: LLM prompt:\n%s", prompt)

        try:
            response = self._llm.invoke(prompt)
            content = response.content.strip()

            # Strip markdown code fences if the LLM wrapped the JSON
            content = re.sub(r"^```(?:json)?\s*\n?", "", content)
            content = re.sub(r"\n?```\s*$", "", content)

            data = json.loads(content)

            from datetime import datetime as _dt
            func = GeneratorFunction(
                name=var_name,
                description=data["description"],
                python_expr=data["python_expr"],
                js_snippet=data["js_snippet"],
                source="llm_generated",
                created_at=_dt.now(tz=timezone.utc).isoformat(),
            )
            return func

        except json.JSONDecodeError as exc:
            logger.error(
                "Agent3 (LLM): could not parse JSON for '%s': %s\nRaw: %s",
                var_name, exc, response.content[:300],
            )
        except KeyError as exc:
            logger.error(
                "Agent3 (LLM): missing field %s in response for '%s'", exc, var_name
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent3 (LLM): unexpected error for '%s': %s", var_name, exc)

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_var_refs(step: TestStep) -> Set[str]:
        """Collect all ``{{varName}}`` references across the entire step."""
        text_parts: list[str] = [step.path]

        for v in (step.path_params or {}).values():
            text_parts.append(str(v))
        for v in (step.query_params or {}).values():
            text_parts.append(str(v))

        def _scan(obj: Any) -> None:
            if isinstance(obj, str):
                text_parts.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _scan(v)
            elif isinstance(obj, list):
                for item in obj:
                    _scan(item)

        _scan(step.body)

        refs: Set[str] = set()
        for part in text_parts:
            refs.update(_VAR_RE.findall(part))
        return refs
