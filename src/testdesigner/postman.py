"""Postman collection and environment generation."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from src.testdesigner.generators import policy_for
from src.testdesigner.models import ExecutionReport, ScenarioCard, TestStep, VariableContext
from src.testdesigner.tools import collect_refs

POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


class PostmanGenerator:
    def from_execution(self, report: ExecutionReport) -> Dict[str, Any]:
        return {
            "info": self._info(report.scenario_name),
            "item": [
                {
                    "name": report.scenario_name,
                    "description": "\n".join(report.reasoning),
                    "item": [self._item_from_record(record) for record in report.successful_requests],
                }
            ],
            "variable": self._variables(report.variables),
        }

    def from_plan(self, card: ScenarioCard) -> Dict[str, Any]:
        return {
            "info": self._info(card.scenario_name),
            "item": [
                {
                    "name": card.scenario_name,
                    "description": card.business_context,
                    "item": [self._item_from_step(step) for step in card.steps],
                }
            ],
            "variable": [{"key": k, "value": self._string(v), "type": "any"} for k, v in card.constant_variables.items()],
        }

    def environment(self, context: VariableContext, name: str = "Generated Environment") -> Dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "name": name,
            "values": [
                {"key": key, "value": self._string(value), "type": "default", "enabled": True}
                for key, value in sorted(context.values().items())
            ],
            "_postman_variable_scope": "environment",
        }

    @staticmethod
    def _info(name: str) -> Dict[str, Any]:
        return {"_postman_id": str(uuid.uuid4()), "name": name, "schema": POSTMAN_SCHEMA}

    def _item_from_step(self, step: TestStep) -> Dict[str, Any]:
        return {
            "name": f"Step {step.step}: {step.name}",
            "request": self._request(step.method, self._path(step), step.headers, step.query_params, step.request_body),
            "event": [
                {"listen": "prerequest", "script": {"type": "text/javascript", "exec": self._pre_request(step)}},
                {"listen": "test", "script": {"type": "text/javascript", "exec": self._tests(step)}},
            ],
            "response": [],
        }

    def _item_from_record(self, record) -> Dict[str, Any]:
        request = self._request(record.method, record.templated_path, record.templated_headers, record.templated_query_params, record.templated_request_body)
        return {
            "name": f"Step {record.step}: {record.name}",
            "request": request,
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": [f"pm.response.to.have.status({record.response_status});"]}}],
            "response": [
                {
                    "name": "Successful response",
                    "originalRequest": request,
                    "code": record.response_status,
                    "status": str(record.response_status),
                    "header": [{"key": "Content-Type", "value": "application/json"}],
                    "body": json.dumps(record.response_body, ensure_ascii=False, indent=2),
                }
            ],
        }

    def _request(self, method: str, path: str, headers: Dict[str, Any] | None, query: Dict[str, Any] | None, body: Any) -> Dict[str, Any]:
        raw = "{{base_url}}" + path
        query_list = []
        if query:
            query_list = [{"key": k, "value": self._string(v)} for k, v in query.items()]
            raw += "?" + "&".join(f"{q['key']}={q['value']}" for q in query_list)
        req: Dict[str, Any] = {
            "method": method,
            "header": [{"key": k, "value": self._string(v)} for k, v in (headers or {}).items()],
            "url": {"raw": raw, "host": ["{{base_url}}"], "path": [p for p in path.strip("/").split("/") if p]},
        }
        if query_list:
            req["url"]["query"] = query_list
        if body is not None:
            req["body"] = {"mode": "raw", "raw": json.dumps(body, ensure_ascii=False, indent=2), "options": {"raw": {"language": "json"}}}
        return req

    @staticmethod
    def _path(step: TestStep) -> str:
        path = step.path
        for name, value in step.path_params.items():
            path = path.replace("{" + name + "}", str(value))
        return path

    def _pre_request(self, step: TestStep) -> List[str]:
        lines: List[str] = []
        for ref in sorted(collect_refs([step.path, step.path_params, step.query_params, step.request_body])):
            policy = policy_for(ref)
            if ref == policy.name or "date" in ref.lower():
                lines.extend(policy.js_snippet.splitlines())
                lines.append("")
        return lines

    def _tests(self, step: TestStep) -> List[str]:
        lines = [f"pm.response.to.have.status({step.expected_status});", "const json = pm.response.json();"]
        for rule in step.extract:
            lines.append(f"pm.collectionVariables.set('{rule.name}', {self._jsonpath_to_js(rule.expression)});")
        for assertion in step.assertions:
            expr = self._jsonpath_to_js(assertion.path)
            if assertion.operator == "not_null":
                lines.append(f"pm.expect({expr}).to.not.be.null;")
            elif assertion.operator == "eq":
                lines.append(f"pm.expect(String({expr})).to.equal(String({json.dumps(assertion.expected, ensure_ascii=False)}));")
        return lines

    @staticmethod
    def _jsonpath_to_js(path: str) -> str:
        if path == "$":
            return "json"
        if path.startswith("$."):
            return "json." + path[2:]
        if path.startswith("$["):
            return "json" + path[1:]
        return "json." + path.lstrip("$.")

    @staticmethod
    def _variables(context: VariableContext) -> List[Dict[str, Any]]:
        return [{"key": k, "value": PostmanGenerator._string(v), "type": "any"} for k, v in sorted(context.values().items())]

    @staticmethod
    def _string(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return "" if value is None else str(value)
