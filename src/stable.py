from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from domain import DataBindingPlan, ExecutorTrace, ProjectState, ProvidedState, ScenarioRunOutput
from executor import FlowExecutor
from generators import GeneratorRegistry
from io_utils import write_json


def scenario_id(scenario_path: str | Path) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", Path(scenario_path).stem).strip("_")


def stable_package_dir(stable_dir: str | Path, scenario_path: str | Path) -> Path:
    return Path(stable_dir) / scenario_id(scenario_path)


def publish_stable_package(
    state: ProjectState,
    stable_dir: str | Path,
    openapi_path: str | Path,
    test_data_path: str | Path | None,
    base_url: str,
) -> Path | None:
    if not state.stabilization or state.stabilization.status != "passed":
        return None
    if not state.stabilization.stable_plan or not state.stabilization.attempts:
        return None

    package_dir = stable_package_dir(stable_dir, state.scenario.path)
    latest_trace = state.stabilization.attempts[-1].trace
    provided_state = build_provided_state(state)
    output = ScenarioRunOutput(
        scenario_path=state.scenario.path,
        status="passed",
        provided_state=provided_state,
        stable_plan=state.stabilization.stable_plan,
    )
    metadata = {
        "scenario_path": state.scenario.path,
        "scenario_id": scenario_id(state.scenario.path),
        "scenario_hash": file_sha256(state.scenario.path),
        "openapi_path": str(openapi_path),
        "openapi_hash": file_sha256(openapi_path),
        "test_data_path": str(test_data_path) if test_data_path else None,
        "test_data_hash": file_sha256(test_data_path) if test_data_path else None,
        "base_url": base_url,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "passed",
    }

    write_json(package_dir / "metadata.json", metadata)
    write_json(package_dir / "stable_plan.json", state.stabilization.stable_plan.model_dump(mode="json"))
    write_json(package_dir / "last_success_trace.json", latest_trace.model_dump(mode="json"))
    write_json(package_dir / "provided_state.json", output.model_dump(mode="json"))
    write_json(package_dir / "review_notes.json", state.stabilization.review_notes)
    return package_dir


def load_stable_plan(package_dir: str | Path) -> DataBindingPlan:
    data = json.loads((Path(package_dir) / "stable_plan.json").read_text(encoding="utf-8"))
    return DataBindingPlan.model_validate(data)


def load_scenario_output(package_dir: str | Path) -> ScenarioRunOutput:
    data = json.loads((Path(package_dir) / "provided_state.json").read_text(encoding="utf-8"))
    return ScenarioRunOutput.model_validate(data)


def validate_stable_package(
    package_dir: str | Path,
    scenario_path: str | Path,
    openapi_path: str | Path,
    test_data_path: str | Path | None,
) -> list[str]:
    package = Path(package_dir)
    missing = [
        name
        for name in ["metadata.json", "stable_plan.json", "provided_state.json"]
        if not (package / name).exists()
    ]
    if missing:
        return [f"missing stable artifact(s): {', '.join(missing)}"]

    metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
    issues = []
    if metadata.get("status") != "passed":
        issues.append("stable package status is not passed")
    if metadata.get("scenario_hash") != file_sha256(scenario_path):
        issues.append("scenario file changed after stable package was published")
    if metadata.get("openapi_hash") != file_sha256(openapi_path):
        issues.append("OpenAPI file changed after stable package was published")
    expected_test_data_hash = file_sha256(test_data_path) if test_data_path else None
    if metadata.get("test_data_hash") != expected_test_data_hash:
        issues.append("test data file changed after stable package was published")
    return issues


def execute_stable_setup(
    package_dir: str | Path,
    base_url: str,
    static_test_data: dict[str, Any],
    external_context: dict[str, Any] | None = None,
    generator_registry: GeneratorRegistry | None = None,
) -> tuple[ExecutorTrace, dict[str, Any]]:
    plan = load_stable_plan(package_dir)
    executor = FlowExecutor(
        base_url=base_url,
        static_test_data=static_test_data,
        external_context=external_context or {},
        generator_registry=generator_registry or GeneratorRegistry(),
    )
    trace = executor.execute(plan, attempt=1)
    return trace, trace.variables if trace.status == "passed" else {}


def build_provided_state(state: ProjectState) -> list[ProvidedState]:
    if not state.stabilization or not state.stabilization.attempts:
        return []
    latest_trace = state.stabilization.attempts[-1].trace
    provided = []
    for variable, value in latest_trace.variables.items():
        source_step_id = None
        json_path = None
        for step in latest_trace.steps:
            if variable in step.extracted_variables:
                source_step_id = step.step_id
                break
        if state.data_binding and source_step_id:
            try:
                step_index = int(source_step_id.removeprefix("s")) - 1
            except ValueError:
                step_index = -1
            if 0 <= step_index < len(state.data_binding.steps):
                for extraction in state.data_binding.steps[step_index].response_extractions:
                    if extraction.variable == variable:
                        json_path = extraction.json_path
                        break
        provided.append(
            ProvidedState(
                name=variable,
                value=value,
                semantic_type=semantic_type(variable),
                source_scenario=state.scenario.path,
                source_step_id=source_step_id,
                json_path=json_path,
            )
        )
    return provided


def semantic_type(variable: str) -> str | None:
    normalized = variable.lower()
    if "reservation" in normalized:
        return "reservation_id"
    if "rental" in normalized:
        return "rental_id"
    if "payment" in normalized:
        return "payment_id"
    if normalized.endswith("_id") or normalized.endswith("id"):
        return normalized
    return None


def file_sha256(path: str | Path | None) -> str | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    return hashlib.sha256(file_path.read_bytes()).hexdigest()
