from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.documentation_analyst import DocumentationAnalystAgent
from domain import AgentRun, ProjectState, ScenarioDependency
from generators import GeneratorRegistry
from io_utils import ArtifactStore, load_scenario, load_test_data
from llm import LLM
from openapi import load_openapi_operations
from orchestrator import AgenticTestDesignOrchestrator
from stable import (
    execute_stable_setup,
    load_scenario_output,
    scenario_id,
    stable_package_dir,
    validate_stable_package,
)


class ScenarioDependencyRunner:
    """Runs target scenario only after required stable scenario packages are executed as setup."""

    def __init__(self, llm: LLM | None = None):
        self.llm = llm
        self.generator_registry = GeneratorRegistry()

    def run(
        self,
        scenario_path: str,
        openapi_path: str,
        out_dir: str | Path,
        test_data_path: str | None = None,
        base_url: str = "http://localhost:8000",
        max_attempts: int = 7,
        stable_dir: str | Path = "runs/stable",
    ) -> ProjectState:
        store = ArtifactStore(out_dir)
        store.reset_log()
        store.log_event("Dependency-aware run started", scenario=scenario_path, stable_dir=stable_dir)

        state = ProjectState(
            scenario=load_scenario(scenario_path),
            operations=load_openapi_operations(openapi_path),
            static_test_data=load_test_data(test_data_path),
        )

        analyst = DocumentationAnalystAgent(self.llm)
        state, run = analyst.run(state)
        state.agent_runs.append(run)
        store.save_agent_run(run, "dependency_preflight/documentation_analyst")
        store.log_event("Dependency preflight finished", status=run.status)
        if run.status != "completed":
            store.save_state(state)
            store.log_event("Dependency-aware run stopped", reason="dependency_preflight_failed")
            return state

        dependencies = [
            item
            for item in (state.understanding.scenario_dependencies if state.understanding else [])
            if item.kind == "requires_scenario"
        ]
        store.log_event("Scenario dependencies discovered", count=len(dependencies))
        if not dependencies:
            return AgenticTestDesignOrchestrator(self.llm).run(
                scenario_path,
                openapi_path,
                out_dir,
                test_data_path,
                base_url=base_url,
                max_attempts=max_attempts,
                stable_dir=stable_dir,
                publish_stable=True,
                reset_log=False,
            )

        external_context: dict[str, Any] = {}
        for dependency in dependencies:
            dependency_path = _resolve_dependency_path(dependency, Path(scenario_path).parent)
            if not dependency_path:
                _block(state, store, f"Cannot resolve dependency scenario: {dependency.reference}")
                return state

            package_dir = stable_package_dir(stable_dir, dependency_path)
            issues = validate_stable_package(package_dir, dependency_path, openapi_path, test_data_path)
            if issues:
                _block(
                    state,
                    store,
                    f"Dependency scenario is not stabilized: {dependency_path}. Issues: {'; '.join(issues)}",
                )
                return state

            store.log_event(
                "Executing stable dependency setup",
                dependency=dependency_path,
                package=package_dir,
            )
            trace, variables = execute_stable_setup(
                package_dir,
                base_url=base_url,
                static_test_data=state.static_test_data,
                external_context=external_context,
                generator_registry=self.generator_registry,
            )
            store.save_json(
                f"dependency_setups/{scenario_id(dependency_path)}_trace.json",
                trace.model_dump(mode="json"),
            )
            store.log_event(
                "Stable dependency setup finished",
                dependency=dependency_path,
                status=trace.status,
                variables=len(variables),
            )
            if trace.status != "passed":
                _block(
                    state,
                    store,
                    f"Stable dependency setup failed for {dependency_path}: {trace.failure}",
                )
                return state
            external_context.update(_context_from_setup(package_dir, variables))

        store.log_event("External context prepared", keys=",".join(sorted(external_context)) or "-")
        return AgenticTestDesignOrchestrator(self.llm).run(
            scenario_path,
            openapi_path,
            out_dir,
            test_data_path,
            base_url=base_url,
            max_attempts=max_attempts,
            external_context=external_context,
            stable_dir=stable_dir,
            publish_stable=True,
            reset_log=False,
        )


def _resolve_dependency_path(dependency: ScenarioDependency, scenario_dir: Path) -> str | None:
    if not dependency.reference:
        return None
    reference = Path(dependency.reference)
    candidates = []
    if reference.is_absolute():
        candidates.append(reference)
    else:
        candidates.append(scenario_dir / reference)
        candidates.append(scenario_dir / f"{dependency.reference}.md")
        for item in scenario_dir.glob("*.md"):
            if item.stem == dependency.reference or dependency.reference in item.stem:
                candidates.append(item)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def _context_from_setup(package_dir: str | Path, variables: dict[str, Any]) -> dict[str, Any]:
    context = dict(variables)
    scenario_output = load_scenario_output(package_dir)
    for item in scenario_output.provided_state:
        if item.name in variables:
            context[item.name] = variables[item.name]
            if item.semantic_type:
                context.setdefault(item.semantic_type, variables[item.name])
    return context


def _block(state: ProjectState, store: ArtifactStore, reason: str) -> None:
    run = AgentRun(
        agent_name="Scenario Dependency Runner",
        status="failed",
        notes=[reason],
    )
    state.agent_runs.append(run)
    store.save_agent_run(run, "scenario_dependency_runner")
    store.save_state(state)
    store.log_event("Dependency-aware run stopped", reason=reason)
