"""
Agent 3 — Data Generator.

Generates or regenerates runtime variable values that are either:

* **Missing** — referenced as ``{{varName}}`` in a step but not yet in context.
* **Stale / invalid** — a previous attempt with the current values failed, so
  the agent tries to produce fresh data.

Current implementation is rule-based (no LLM call needed for the carsharing
scenario).  An LLM-based fallback is available for unknown variable names.

Known generators
----------------
``startDate``
    ISO-8601 date-time string 24 h in the future, e.g.
    ``"2026-05-17T10:00:00Z"``.  Always regenerated on retry (clock skew fix).

``vehicleId``, ``bookingId``
    Extracted from API responses by Agent 2; not generated here unless missing
    from context (which would indicate a logic error in a previous step).

LLM-based fallback
------------------
If Agent 3 encounters an unknown variable name it falls back to the configured
LLM and asks it to produce a sensible value given the step description.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Set

from src.models.scenario import TestStep
from src.utils.config import AppConfig

logger = logging.getLogger(__name__)


class DataGeneratorAgent:
    """
    Agent 3: generates missing or refreshed variable values for a test step.

    Args:
        config: Application configuration (used for the LLM fallback).
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._llm: Any = None  # lazy-loaded only if needed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            missing_vars: Variable names that are referenced in the step but
                          not present in *context*.

        Returns:
            A dict mapping variable names to generated values.  Merge this
            into the context before re-executing the step.
        """
        generated: Dict[str, Any] = {}

        for var in missing_vars:
            value = self._generate_one(var, step, context)
            if value is not None:
                generated[var] = value
                logger.info(
                    "Agent3: generated  %s = %r  (step %d)",
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
        Regenerate dynamic variables when a step failed.

        Re-generates all time-sensitive variables (``startDate``, etc.) so
        that a retry has fresh data.

        Args:
            step:              The failed step.
            context:           Current variable context.
            error_description: Human-readable failure reason (used for logging
                               and the LLM fallback prompt).

        Returns:
            A dict of refreshed variable values to merge into context.
        """
        logger.info(
            "Agent3: refreshing data for step %d after failure: %s",
            step.step_num, error_description,
        )

        # Collect all {{varName}} references in the step
        all_refs = self._collect_var_refs(step)
        # Re-generate only time-sensitive ones; others stay as-is
        time_sensitive = {"startDate", "endDate", "pickupDate", "returnDate"}
        to_refresh = all_refs & time_sensitive

        return self.generate(step, context, to_refresh)

    # ------------------------------------------------------------------
    # Internal generators
    # ------------------------------------------------------------------

    def _generate_one(
        self,
        var_name: str,
        step: TestStep,
        context: Dict[str, Any],
    ) -> Any:
        """Return a generated value for *var_name*, or ``None`` if unknown."""

        # --- Time values ---
        if var_name in ("startDate", "pickupDate"):
            return self._future_datetime(hours=24)

        if var_name in ("endDate", "returnDate"):
            hours = 24 + int(context.get("booking_duration_days", 1)) * 24
            return self._future_datetime(hours=hours)

        # --- LLM fallback for unknown variables ---
        return self._llm_generate(var_name, step, context)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _future_datetime(hours: int = 24) -> str:
        """Return an ISO-8601 UTC datetime string *hours* from now."""
        dt = datetime.now(tz=timezone.utc) + timedelta(hours=hours)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _collect_var_refs(step: TestStep) -> Set[str]:
        """Collect all ``{{varName}}`` references across the entire step."""
        pattern = re.compile(r"\{\{(\w+)\}\}")
        text_parts: list[str] = [step.path]

        for v in (step.path_params or {}).values():
            text_parts.append(v)
        for v in (step.query_params or {}).values():
            text_parts.append(v)

        def collect_body(obj: Any) -> None:
            if isinstance(obj, str):
                text_parts.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    collect_body(v)
            elif isinstance(obj, list):
                for item in obj:
                    collect_body(item)

        collect_body(step.body)

        refs: Set[str] = set()
        for part in text_parts:
            refs.update(pattern.findall(part))
        return refs

    def _llm_generate(
        self,
        var_name: str,
        step: TestStep,
        context: Dict[str, Any],
    ) -> Any:
        """
        Ask the LLM to generate a value for an unknown variable.

        Used as a last resort when no built-in rule exists.
        """
        if self._llm is None:
            from src.utils.llm_factory import create_llm
            self._llm = create_llm(self.config)

        prompt = (
            f"You are a test data generator. Generate a single value for the "
            f"variable '{{{{ {var_name} }}}}' needed by this API test step.\n\n"
            f"Step description: {step.description}\n"
            f"API path: {step.method} {step.path}\n"
            f"Current context keys: {list(context.keys())}\n\n"
            f"Reply with ONLY the raw value — no explanation, no quotes "
            f"(unless the value is a string that needs them)."
        )

        try:
            response = self._llm.invoke(prompt)
            value = response.content.strip().strip('"').strip("'")
            logger.info("Agent3 (LLM fallback): %s = %r", var_name, value)
            return value
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent3 LLM fallback failed for '%s': %s", var_name, exc)
            return None
