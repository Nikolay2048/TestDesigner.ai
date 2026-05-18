"""Test data generation and runtime variable storage."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.testdesigner.llm import LlmClient
from src.testdesigner.models import RuntimeValue, ScenarioCard, VariableSource
from src.testdesigner.utils import collect_variables, extract_jsonpath

DATA_SYSTEM = """
You are DataAgent. Generate one JSON-compatible test value for a REST API variable.
Respect variable name, source description, scenario context, and already known values.
Return JSON: {"value": ..., "generator": "short_strategy_name"}.
"""


class DataAgent:
    def __init__(self, constants: dict[str, Any], llm: LlmClient | None = None) -> None:
        self.llm = llm
        self.values: dict[str, RuntimeValue] = {
            key: RuntimeValue(name=key, kind="constant", value=value)
            for key, value in constants.items()
        }

    def ensure_for_step(self, card: ScenarioCard, step_index: int, payloads: list[Any]) -> list[str]:
        generated: list[str] = []
        refs: set[str] = set()
        for payload in payloads:
            refs.update(collect_variables(payload))
        for name in sorted(refs):
            if name in self.values:
                continue
            source = card.variables.get(name)
            if source and source.kind == "extracted":
                continue
            value, generator = self._generate(name, card)
            self.values[name] = RuntimeValue(name=name, kind="generated", value=value, generator=generator)
            generated.append(name)
        return generated

    def store_extractions(self, step: int, body: Any, rules: list[tuple[str, str, bool]]) -> list[str]:
        stored: list[str] = []
        for name, expression, required in rules:
            matches = extract_jsonpath(body, expression)
            if not matches:
                if required:
                    raise ValueError(f"{name}: no match for {expression}")
                continue
            self.values[name] = RuntimeValue(
                name=name,
                kind="extracted",
                value=matches[0],
                source_step=step,
                expression=expression,
            )
            stored.append(name)
        return stored

    def snapshot(self) -> dict[str, Any]:
        return {key: item.value for key, item in self.values.items()}

    def _generate(self, name: str, card: ScenarioCard) -> tuple[Any, str]:
        source = card.variables.get(name)
        if source and source.kind == "literal" and source.value is not None:
            return self._normalize_generated(name, source.value, source.generator or "literal", source)
        if self.llm and self.llm.enabled:
            try:
                response = self.llm.json(DATA_SYSTEM, {
                    "variable": name,
                    "source": source.model_dump() if source else None,
                    "known_values": self.snapshot(),
                    "scenario": {"name": card.name, "business_rules": card.business_rules},
                }, timeout=60)
                if "value" in response:
                    return self._normalize_generated(
                        name,
                        response["value"],
                        str(response.get("generator") or "llm"),
                        source,
                    )
            except Exception:
                pass
        return self._fallback(name)

    def _normalize_generated(
        self,
        name: str,
        value: Any,
        generator: str,
        source: VariableSource | None,
    ) -> tuple[Any, str]:
        source_generator = (source.generator if source else "") or ""
        if "future" in source_generator.lower() and self._is_date_variable(name, source):
            parsed = self._parse_datetime(value)
            if parsed is None or parsed <= datetime.now(timezone.utc):
                return self._future_datetime(name)
        return value, generator

    @staticmethod
    def _is_date_variable(name: str, source: VariableSource | None) -> bool:
        hints = [name]
        if source:
            hints.extend([source.name, source.description, source.generator or ""])
        return any("date" in (hint or "").lower() for hint in hints)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _future_datetime(name: str) -> tuple[str, str]:
        lowered = name.lower()
        days = 2 if "end" in lowered else 1
        return (
            (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "future_iso_datetime_guard",
        )

    def _fallback(self, name: str) -> tuple[Any, str]:
        lowered = name.lower()
        if "date" in lowered:
            return self._future_datetime(name)
        if "email" in lowered:
            return f"user_{uuid.uuid4().hex[:10]}@example.test", "synthetic_email"
        if "phone" in lowered:
            return "+79" + str(random.randint(100000000, 999999999)), "synthetic_phone"
        if any(token in lowered for token in ("amount", "price", "sum", "total", "cost", "damage")):
            return 1000.0, "positive_amount"
        if lowered.endswith("id") or lowered.endswith("_id"):
            return f"{name}-{uuid.uuid4().hex[:10]}", "synthetic_id"
        return f"{name}-{uuid.uuid4().hex[:8]}", "synthetic_string"
