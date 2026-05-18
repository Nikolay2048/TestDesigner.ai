"""Postman collection generation."""

from __future__ import annotations

import json
import uuid
from typing import Any

from src.testdesigner.models import ExecutionReport, ScenarioCard, ScenarioStep

SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


class PostmanBuilder:
    def from_card(self, card: ScenarioCard) -> dict[str, Any]:
        return {
            "info": {"_postman_id": str(uuid.uuid4()), "name": card.name, "schema": SCHEMA},
            "item": [self._item(step) for step in card.steps],
            "variable": [{"key": name, "value": self._string(src.value), "type": "any"} for name, src in card.variables.items()],
        }

    def environment(self, report: ExecutionReport) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "name": f"{report.scenario_name} Environment",
            "values": [
                {"key": name, "value": self._string(value.value), "type": "default", "enabled": True}
                for name, value in sorted(report.runtime_values.items())
            ],
            "_postman_variable_scope": "environment",
        }

    def _item(self, step: ScenarioStep) -> dict[str, Any]:
        request = self._request(step)
        return {
            "name": f"{step.step}. {step.name}",
            "request": request,
            "event": [
                {"listen": "test", "script": {"type": "text/javascript", "exec": self._tests(step)}},
            ],
            "response": [],
        }

    def _request(self, step: ScenarioStep) -> dict[str, Any]:
        path = step.path
        for name, value in step.path_params.items():
            path = path.replace("{" + name + "}", str(value))
        raw = "{{base_url}}" + path
        query = [{"key": key, "value": self._string(value)} for key, value in step.query_params.items()]
        if query:
            raw += "?" + "&".join(f"{item['key']}={item['value']}" for item in query)
        request: dict[str, Any] = {
            "method": step.method,
            "header": [{"key": key, "value": self._string(value)} for key, value in step.headers.items()],
            "url": {
                "raw": raw,
                "host": ["{{base_url}}"],
                "path": [part for part in path.strip("/").split("/") if part],
            },
        }
        if query:
            request["url"]["query"] = query
        if step.request_body is not None:
            request["body"] = {
                "mode": "raw",
                "raw": json.dumps(step.request_body, ensure_ascii=False, indent=2),
                "options": {"raw": {"language": "json"}},
            }
        return request

    def _tests(self, step: ScenarioStep) -> list[str]:
        lines = [f"pm.response.to.have.status({step.expected_status});", "const json = pm.response.json();"]
        for rule in step.extract:
            lines.append(f"pm.collectionVariables.set('{rule.name}', {self._jsonpath_js(rule.expression)});")
        return lines

    @staticmethod
    def _jsonpath_js(expression: str) -> str:
        if expression == "$":
            return "json"
        if expression.startswith("$."):
            return "json." + expression[2:]
        if expression.startswith("$["):
            return "json" + expression[1:]
        return "json." + expression.lstrip("$.")

    @staticmethod
    def _string(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
