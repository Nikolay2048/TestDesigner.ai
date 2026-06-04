from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from domain import DataBindingPlan, DesignedTestCase, ProjectState, RequestValueBinding, ResponseExtraction, TestAssertion
from generators import GeneratorRegistry
from io_utils import write_json
from test_design import build_case_execution_plan


POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


def export_postman_artifacts(
    state: ProjectState,
    out_dir: str | Path,
    base_url: str,
    generator_registry: GeneratorRegistry | None = None,
) -> dict[str, Any]:
    """Export stable happy path and designed test cases as Postman artifacts."""

    exporter = PostmanExporter(generator_registry or GeneratorRegistry())
    artifacts = exporter.export(state, base_url)
    postman_dir = Path(out_dir) / "postman"
    write_json(postman_dir / "happy_path.postman_collection.json", artifacts["happy_path_collection"])
    write_json(postman_dir / "test_cases.postman_collection.json", artifacts["test_cases_collection"])
    write_json(postman_dir / "environment.postman_environment.json", artifacts["environment"])
    write_json(postman_dir / "export_summary.json", artifacts["summary"])
    return artifacts["summary"]


class PostmanExporter:
    def __init__(self, generator_registry: GeneratorRegistry | None = None):
        self.generator_registry = generator_registry or GeneratorRegistry()
        self.unsupported_features: list[str] = []
        self.requires_human_review: list[str] = []
        self.scenario_generated_bindings: dict[str, RequestValueBinding] = {}

    def export(self, state: ProjectState, base_url: str) -> dict[str, Any]:
        if not state.stabilization or state.stabilization.status != "passed" or not state.stabilization.stable_plan:
            raise ValueError("Stable happy path is required for Postman export.")

        stable_plan = state.stabilization.stable_plan
        self.scenario_generated_bindings = _scenario_generated_bindings(stable_plan)
        happy_path_collection = self._collection(
            name=f"{state.scenario.title} - Happy Path",
            items=self._happy_path_items(stable_plan),
        )
        test_case_items = []
        if state.test_design:
            test_case_items = self._grouped_test_case_items(stable_plan, state.test_design.test_cases)
        test_cases_collection = self._collection(
            name=f"{state.scenario.title} - Test Cases",
            items=test_case_items,
        )
        environment = self._environment(state.scenario.title, base_url, state.static_test_data)
        summary = {
            "happy_path_requests": len(stable_plan.steps),
            "test_cases": len(state.test_design.test_cases) if state.test_design else 0,
            "test_case_requests": _count_requests(test_case_items),
            "environment_values": len(environment["values"]),
            "unsupported_features": self.unsupported_features,
            "requires_human_review": self.requires_human_review,
            "files": {
                "happy_path_collection": "postman/happy_path.postman_collection.json",
                "test_cases_collection": "postman/test_cases.postman_collection.json",
                "environment": "postman/environment.postman_environment.json",
                "summary": "postman/export_summary.json",
            },
        }
        return {
            "happy_path_collection": happy_path_collection,
            "test_cases_collection": test_cases_collection,
            "environment": environment,
            "summary": summary,
        }

    def _collection(self, name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "info": {
                "_postman_id": str(uuid.uuid4()),
                "name": name,
                "schema": POSTMAN_SCHEMA,
            },
            "item": items,
        }

    def _happy_path_items(self, plan: DataBindingPlan) -> list[dict[str, Any]]:
        return [
            self._request_item(
                name=f"{step_id} {step.business_step}",
                step=step,
                expected_statuses=None,
                review_note=None,
            )
            for step_id, step in _iter_steps(plan)
        ]

    def _test_case_folder(self, stable_plan: DataBindingPlan, case: DesignedTestCase) -> dict[str, Any]:
        try:
            case_plan = build_case_execution_plan(stable_plan, case)
            items = []
            for step_id, step in _iter_steps(case_plan):
                assertions = [assertion for assertion in case.assertions if assertion.step_id == step_id]
                items.append(
                    self._request_item(
                        name=f"{step_id} {step.business_step}",
                        step=step,
                        expected_statuses=None,
                        review_note=None,
                        include_extractions=False,
                        assertions=assertions,
                    )
                )
        except Exception as exc:
            self.unsupported_features.append(f"{case.case_id}: {exc}")
            items = []
        if case.requires_human_review:
            self.requires_human_review.append(f"{case.case_id}: {case.title}")
        return {
            "name": f"{case.case_id} {case.title} ({case.technique})",
            "description": self._case_description(case),
            "item": items,
        }

    def _grouped_test_case_items(
        self,
        stable_plan: DataBindingPlan,
        cases: list[DesignedTestCase],
    ) -> list[dict[str, Any]]:
        deterministic_cases = [case for case in cases if case.technique != "business_rule_violation"]
        business_cases = [case for case in cases if case.technique == "business_rule_violation"]
        groups = []
        if deterministic_cases:
            groups.append(
                {
                    "name": "Deterministic checks",
                    "description": (
                        "Checks built from OpenAPI/schema and universal test-design techniques."
                    ),
                    "item": [self._test_case_folder(stable_plan, case) for case in deterministic_cases],
                }
            )
        if business_cases:
            groups.append(
                {
                    "name": "LLM business checks",
                    "description": (
                        "Checks proposed by the LLM from business rules and validated by deterministic code."
                    ),
                    "item": [self._test_case_folder(stable_plan, case) for case in business_cases],
                }
            )
        return groups

    def _request_item(
        self,
        name: str,
        step,
        expected_statuses: list[int] | None,
        review_note: str | None,
        include_extractions: bool = True,
        assertions: list[TestAssertion] | None = None,
    ) -> dict[str, Any]:
        body, query, headers, path = self._request_parts(step)
        pre_request = self._pre_request_script(step.request_bindings)
        extractions = step.response_extractions if include_extractions else []
        tests = (
            self._test_script_from_assertions(assertions)
            if assertions is not None
            else self._test_script(extractions, expected_statuses, review_note)
        )
        item = {
            "name": name,
            "request": {
                "method": step.operation.method.upper(),
                "header": [{"key": key, "value": value} for key, value in headers.items()],
                "url": self._url(path, query),
            },
            "event": [
                {"listen": "prerequest", "script": {"type": "text/javascript", "exec": pre_request}},
                {"listen": "test", "script": {"type": "text/javascript", "exec": tests}},
            ],
        }
        if body is not None:
            item["request"]["body"] = {
                "mode": "raw",
                "raw": _json_dumps(body),
                "options": {"raw": {"language": "json"}},
            }
            item["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
        return item

    def _request_parts(self, step) -> tuple[dict[str, Any] | None, dict[str, str], dict[str, str], str]:
        body: dict[str, Any] = {}
        query: dict[str, str] = {}
        headers: dict[str, str] = {}
        path = step.operation.path
        for binding in step.request_bindings:
            value = self._binding_template(binding)
            if binding.location == "path":
                param_name = binding.target.removeprefix("$.path.")
                path = path.replace("{" + param_name + "}", value)
            elif binding.location == "query":
                query[_field_name(binding.target)] = value
            elif binding.location == "header":
                headers[_field_name(binding.target)] = value
            elif binding.location == "body":
                _set_json_path(body, binding.target, value)
        return (body or None), query, headers, path

    def _binding_template(self, binding: RequestValueBinding) -> Any:
        if binding.source == "literal":
            return binding.literal
        variable = _binding_variable(binding)
        return "{{" + variable + "}}"

    def _pre_request_script(self, bindings: list[RequestValueBinding]) -> list[str]:
        lines: list[str] = []
        used_generators: set[str] = set()
        for binding in bindings:
            if binding.source == "generated" and binding.generator:
                binding_for_generation = self.scenario_generated_bindings.get(_binding_variable(binding), binding)
                generator_name = binding_for_generation.generator or binding.generator
                js_name = _js_generator_name(generator_name)
                if generator_name not in used_generators:
                    try:
                        lines.append(self.generator_registry.js_source(generator_name))
                    except KeyError:
                        self.unsupported_features.append(f"Unsupported generator for Postman: {generator_name}")
                        continue
                    used_generators.add(generator_name)
                variable = _binding_variable(binding)
                params = ", ".join(
                    _js_literal(value)
                    for value in _generator_arg_values(
                        generator_name,
                        binding_for_generation.params,
                    )
                )
                call = f"{js_name}({params})"
                if binding.scope == "scenario":
                    lines.extend(
                        [
                            f"if (pm.collectionVariables.get('{variable}') === undefined) {{",
                            f"  pm.collectionVariables.set('{variable}', {call});",
                            "}",
                        ]
                    )
                else:
                    lines.append(f"pm.variables.set('{variable}', {call});")
            elif binding.source == "computed":
                self.unsupported_features.append(f"Computed binding requires review: {binding.target}")
                lines.append(f"// REVIEW: computed binding is not exported automatically: {binding.target}")
        return lines or ["// No generated values for this request."]

    def _test_script(
        self,
        extractions: list[ResponseExtraction],
        expected_statuses: list[int] | None,
        review_note: str | None,
    ) -> list[str]:
        lines = []
        if expected_statuses:
            lines.extend(
                [
                    "pm.test('Status matches expected response', function () {",
                    f"  pm.expect({expected_statuses}).to.include(pm.response.code);",
                    "});",
                ]
            )
        else:
            lines.extend(
                [
                    "pm.test('Status is 2xx', function () {",
                    "  pm.expect(pm.response.code).to.be.within(200, 299);",
                    "});",
                ]
            )
        if review_note:
            lines.append(f"// REVIEW: {review_note}")
        if extractions:
            lines.append("const json = pm.response.json();")
            for extraction in extractions:
                accessor = _json_path_accessor("json", extraction.json_path)
                lines.extend(
                    [
                        f"pm.test('{extraction.variable} extracted', function () {{",
                        f"  pm.expect({accessor}).to.not.equal(undefined);",
                        "});",
                        f"pm.collectionVariables.set('{extraction.variable}', {accessor});",
                    ]
                )
        return lines

    def _test_script_from_assertions(self, assertions: list[TestAssertion]) -> list[str]:
        if not assertions:
            return ["// No assertions generated for this request."]
        lines: list[str] = []
        needs_json = any(assertion.kind.startswith("json_path_") for assertion in assertions)
        if needs_json:
            lines.append("const json = pm.response.json();")
        for assertion in assertions:
            if assertion.kind == "status_2xx":
                lines.extend(
                    [
                        "pm.test('Status is 2xx', function () {",
                        "  pm.expect(pm.response.code).to.be.within(200, 299);",
                        "});",
                    ]
                )
            elif assertion.kind == "status_in":
                expected = assertion.expected if isinstance(assertion.expected, list) else []
                lines.extend(
                    [
                        "pm.test('Status matches expected response', function () {",
                        f"  pm.expect({expected}).to.include(pm.response.code);",
                        "});",
                    ]
                )
            elif assertion.kind == "json_path_exists" and assertion.json_path:
                accessor = _json_path_accessor("json", assertion.json_path)
                lines.extend(
                    [
                        f"pm.test('{assertion.json_path} exists', function () {{",
                        f"  pm.expect({accessor}).to.not.equal(undefined);",
                        "});",
                    ]
                )
            elif assertion.kind == "json_path_type" and assertion.json_path:
                accessor = _json_path_accessor("json", assertion.json_path)
                lines.extend(_json_type_assertion_lines(accessor, assertion.expected, assertion.json_path))
            elif assertion.kind == "manual_review":
                lines.append(f"// REVIEW: {assertion.description}")
        return lines

    def _url(self, path: str, query: dict[str, str]) -> dict[str, Any]:
        raw = "{{baseUrl}}" + path
        if query:
            raw += "?" + "&".join(f"{key}={value}" for key, value in query.items())
        return {
            "raw": raw,
            "host": ["{{baseUrl}}"],
            "path": [part for part in path.lstrip("/").split("/") if part],
            "query": [{"key": key, "value": value} for key, value in query.items()],
        }

    def _environment(self, scenario_title: str, base_url: str, static_test_data: dict[str, Any]) -> dict[str, Any]:
        values = [{"key": "baseUrl", "value": base_url, "type": "default", "enabled": True}]
        for key, value in sorted(static_test_data.items()):
            values.append(
                {
                    "key": _variable_name(key),
                    "value": value if isinstance(value, str) else _json_dumps(value),
                    "type": "default",
                    "enabled": True,
                }
            )
        return {
            "id": str(uuid.uuid4()),
            "name": f"{scenario_title} Environment",
            "values": values,
            "_postman_variable_scope": "environment",
            "_postman_exported_using": "TestDesignerAI",
        }

    def _case_description(self, case: DesignedTestCase) -> str:
        lines = [
            f"Technique: {case.technique}",
            f"Type: {case.type}",
            f"Priority: {case.priority}",
            f"Mutation: {case.mutation.action} {case.mutation.target}",
            "",
            "Expected:",
            *case.expected_result,
        ]
        return "\n".join(lines)


def _iter_steps(plan: DataBindingPlan):
    for index, step in enumerate(plan.steps, start=1):
        yield f"s{index:02d}", step


def _scenario_generated_bindings(plan: DataBindingPlan) -> dict[str, RequestValueBinding]:
    bindings: dict[str, RequestValueBinding] = {}
    for step in plan.steps:
        for binding in step.request_bindings:
            if binding.source == "generated" and binding.scope == "scenario" and binding.generator:
                bindings.setdefault(_binding_variable(binding), binding)
    return bindings


def _count_requests(items: list[dict[str, Any]]) -> int:
    count = 0
    for item in items:
        children = item.get("item")
        if children:
            count += _count_requests(children)
        elif "request" in item:
            count += 1
    return count


def _accepted_statuses_for_case(
    case: DesignedTestCase,
    step_id: str,
    expected_failure_steps: set[str],
) -> list[int] | None:
    if step_id not in expected_failure_steps:
        return None
    statuses = list(dict.fromkeys(case.expected.accepted_statuses))
    if case.expected.status is not None and case.expected.status not in statuses:
        statuses.insert(0, case.expected.status)
    if statuses:
        return statuses
    if case.type == "negative":
        return [400, 404, 409, 422]
    return None


def _expected_failure_step_ids(case: DesignedTestCase) -> set[str]:
    index = _step_index(case.mutated_step_id)
    if index is None:
        return {case.mutated_step_id}
    if case.mutation.action == "skip_setup_step" and case.mutation.skipped_step_id:
        skipped_index = _step_index(case.mutation.skipped_step_id)
        if skipped_index is not None and skipped_index < index:
            return {f"s{index:02d}"}
    if case.mutation.action == "repeat_step":
        return {f"s{index + max(1, case.mutation.repeat_count):02d}"}
    return {case.mutated_step_id}


def _step_index(step_id: str) -> int | None:
    try:
        return int(step_id.removeprefix("s")) - 1
    except ValueError:
        return None


def _review_note(case: DesignedTestCase) -> str | None:
    if not case.requires_human_review:
        return None
    if case.expected.description:
        return case.expected.description
    return "Expected result requires human review."


def _binding_variable(binding: RequestValueBinding) -> str:
    if binding.variable:
        return _variable_name(binding.variable)
    if binding.static_key:
        return _variable_name(binding.static_key)
    return _variable_name(binding.target)


def _variable_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")


def _field_name(target: str) -> str:
    return target.split(".")[-1]


def _set_json_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in path.removeprefix("$.").split(".") if part]
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    if parts:
        current[parts[-1]] = value


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def _js_literal(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _js_generator_name(name: str) -> str:
    return {
        "uuid": "uuid",
        "email": "email",
        "phone_number": "phoneNumber",
        "full_name": "fullName",
        "driver_license_number": "driverLicenseNumber",
        "payment_card_token": "paymentCardToken",
        "date_after_now": "dateAfterNow",
        "random_int": "randomInt",
        "enum_value": "enumValue",
    }.get(name, name)


def _generator_arg_values(name: str, params: dict[str, Any]) -> list[Any]:
    defaults = {
        "email": {"domain": "example.test"},
        "phone_number": {"country": "RU", "format": "e164"},
        "driver_license_number": {"country": "RU"},
        "payment_card_token": {"provider": "mock"},
        "date_after_now": {"days": 1, "format": "iso_datetime"},
        "random_int": {"min": 0, "max": 1000},
    }
    order = {
        "email": ["domain"],
        "phone_number": ["country", "format"],
        "driver_license_number": ["country"],
        "payment_card_token": ["provider"],
        "date_after_now": ["days", "format"],
        "random_int": ["min", "max"],
        "enum_value": ["values"],
    }.get(name, [])
    merged = {**defaults.get(name, {}), **params}
    values = [merged[key] for key in order if key in merged]
    values.extend(value for key, value in params.items() if key not in order)
    return values


def _json_path_accessor(root: str, path: str) -> str:
    parts = _json_path_parts(path)
    current = root
    for part in parts:
        if part == "[]":
            current += "[0]"
        elif isinstance(part, int):
            current += f"[{part}]"
        else:
            current += f"[{_js_literal(part)}]"
    return current


def _json_type_assertion_lines(accessor: str, expected_type: Any, label: str) -> list[str]:
    if expected_type == "array":
        condition = f"Array.isArray({accessor})"
    elif expected_type == "integer":
        condition = f"Number.isInteger({accessor})"
    elif expected_type == "number":
        condition = f"typeof {accessor} === 'number'"
    elif expected_type == "boolean":
        condition = f"typeof {accessor} === 'boolean'"
    elif expected_type == "object":
        condition = f"typeof {accessor} === 'object' && !Array.isArray({accessor}) && {accessor} !== null"
    elif expected_type == "string":
        condition = f"typeof {accessor} === 'string'"
    else:
        return [f"// REVIEW: unsupported OpenAPI type assertion for {label}: {expected_type}"]
    return [
        f"pm.test('{label} has type {expected_type}', function () {{",
        f"  pm.expect({condition}).to.eql(true);",
        "});",
    ]


def _json_path_parts(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for raw in path.removeprefix("$.").split("."):
        if raw.endswith("[]"):
            parts.append(raw[:-2])
            parts.append("[]")
            continue
        match = re.fullmatch(r"(.+)\[(\d+)]", raw)
        if match:
            parts.append(match.group(1))
            parts.append(int(match.group(2)))
        elif raw:
            parts.append(raw)
    return parts
