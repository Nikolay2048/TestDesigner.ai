from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from agents.documentation_analyst import DocumentationAnalystAgent
from domain import AgentRun, DataBindingPlan, ProjectState, ResponseExtraction, ScenarioDependency
from generators import GeneratorRegistry
from io_utils import ArtifactStore, load_scenario, load_test_data
from llm import LLM
from openapi import load_openapi_operations
from orchestrator import AgenticTestDesignOrchestrator
from stable import (
    execute_stable_setup,
    load_dependency_setup_plan,
    load_stable_plan,
    load_scenario_output,
    scenario_id,
    semantic_type,
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
        max_fixer_tries: int = 7,
        stable_dir: str | Path = "runs/stable",
        run_test_cases: bool = False,
        export_postman: bool = False,
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
        added_required_data = _add_path_placeholders_to_dependencies(state)
        if added_required_data:
            store.log_event(
                "Dependency required_data augmented",
                values=",".join(added_required_data),
            )

        dependencies = _setup_dependencies(state)
        store.log_event("Scenario dependencies discovered", count=len(dependencies))
        if not dependencies:
            return AgenticTestDesignOrchestrator(self.llm).run(
                scenario_path,
                openapi_path,
                out_dir,
                test_data_path,
                base_url=base_url,
                max_attempts=max_attempts,
                max_fixer_tries=max_fixer_tries,
                stable_dir=stable_dir,
                publish_stable=True,
                reset_log=False,
                run_test_cases=run_test_cases,
                export_postman=export_postman,
            )

        dependency_specs = []
        for dependency in dependencies:
            dependency_path = _resolve_dependency_path(
                dependency,
                Path(scenario_path).parent,
                Path(stable_dir),
            )
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
            dependency_specs.append((dependency, dependency_path, package_dir))

        def prepare_external_context(label: str) -> dict[str, Any] | None:
            external_context: dict[str, Any] = {}
            for dependency, dependency_path, package_dir in dependency_specs:
                store.log_event(
                    "Executing stable dependency setup",
                    dependency=dependency_path,
                    package=package_dir,
                    label=label,
                )
                step_limit = _stable_setup_step_limit(package_dir, dependency)
                if step_limit:
                    store.log_event(
                        "Stable dependency setup checkpoint selected",
                        dependency=dependency_path,
                        step_limit=step_limit,
                        label=label,
                    )
                trace, variables = execute_stable_setup(
                    package_dir,
                    base_url=base_url,
                    static_test_data=state.static_test_data,
                    external_context=external_context,
                    generator_registry=self.generator_registry,
                    step_limit=step_limit,
                )
                store.save_json(
                    f"dependency_setups/{label}_{scenario_id(dependency_path)}_trace.json",
                    trace.model_dump(mode="json"),
                )
                store.log_event(
                    "Stable dependency setup finished",
                    dependency=dependency_path,
                    status=trace.status,
                    variables=len(variables),
                    label=label,
                )
                if trace.status != "passed":
                    _block(
                        state,
                        store,
                        f"Stable dependency setup failed for {dependency_path}: {trace.failure}",
                    )
                    return None
                external_context.update(_context_from_setup(package_dir, variables))
            return external_context

        external_context = prepare_external_context("initial")
        if external_context is None:
            return state
        dependency_setup_plan = _build_dependency_setup_plan(dependency_specs)

        store.log_event("External context prepared", keys=",".join(sorted(external_context)) or "-")
        return AgenticTestDesignOrchestrator(self.llm).run(
            scenario_path,
            openapi_path,
            out_dir,
            test_data_path,
            base_url=base_url,
            max_attempts=max_attempts,
            max_fixer_tries=max_fixer_tries,
            external_context=external_context,
            external_context_factory=lambda attempt: prepare_external_context(f"attempt_{attempt:02d}") or {},
            dependency_setup_plan=dependency_setup_plan,
            stable_dir=stable_dir,
            publish_stable=True,
            reset_log=False,
            run_test_cases=run_test_cases,
            export_postman=export_postman,
        )


def _build_dependency_setup_plan(dependency_specs) -> DataBindingPlan:
    """Build the reproducible setup chain used by exported Postman collections."""

    combined = DataBindingPlan()
    for dependency, _dependency_path, package_dir in dependency_specs:
        plan = copy.deepcopy(load_stable_plan(package_dir))
        step_limit = _stable_setup_step_limit(package_dir, dependency)
        if step_limit is not None:
            plan.steps = plan.steps[:step_limit]
        _add_external_context_alias_extractions(plan, package_dir)
        nested_setup = copy.deepcopy(load_dependency_setup_plan(package_dir))
        combined.steps.extend(nested_setup.steps)
        combined.steps.extend(plan.steps)
    return combined


def _add_external_context_alias_extractions(
    plan: DataBindingPlan,
    package_dir: str | Path,
) -> None:
    """Expose semantic aliases such as reservation_id from dependency responses."""

    output = load_scenario_output(package_dir)
    for item in output.provided_state:
        step_number = _step_number(item.source_step_id or "")
        if not step_number or step_number > len(plan.steps) or not item.json_path:
            continue
        step = plan.steps[step_number - 1]
        semantic = item.semantic_type
        if semantic in {None, "id"}:
            semantic = _infer_semantic_type_from_stable_plan(output, item.name)
        aliases = _provided_state_aliases(item.name, semantic)
        existing = {extraction.variable for extraction in step.response_extractions}
        for alias in aliases:
            if alias in existing:
                continue
            step.response_extractions.append(
                ResponseExtraction(
                    variable=alias,
                    json_path=item.json_path,
                    scope="scenario",
                    source_step_id=f"s{step_number:02d}",
                    policy="dependency_context_alias",
                    reason="Required by a dependent scenario.",
                )
            )
            existing.add(alias)


def _resolve_dependency_path(
    dependency: ScenarioDependency,
    scenario_dir: Path,
    stable_dir: Path | None = None,
) -> str | None:
    if not dependency.reference:
        return _resolve_dependency_from_stable_packages(dependency, stable_dir)
    reference = Path(dependency.reference)
    reference_text = dependency.reference.strip()
    candidates = []
    if reference.is_absolute():
        candidates.append(reference)
    else:
        candidates.append(scenario_dir / reference)
        candidates.append(scenario_dir / f"{reference_text}.md")
        for item in scenario_dir.glob("*.md"):
            if _dependency_reference_matches_file(reference_text, item):
                candidates.append(item)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return _resolve_dependency_from_stable_packages(dependency, stable_dir)


def _resolve_dependency_from_stable_packages(
    dependency: ScenarioDependency,
    stable_dir: Path | None,
) -> str | None:
    if not stable_dir or not stable_dir.exists():
        return None

    desired_keys = _dependency_desired_keys(dependency)
    scored: list[tuple[int, str]] = []
    for package_dir in stable_dir.iterdir():
        if not package_dir.is_dir():
            continue
        try:
            scenario_output = load_scenario_output(package_dir)
        except Exception:
            continue
        provided_keys: set[str] = set()
        for item in scenario_output.provided_state:
            semantic = item.semantic_type
            if semantic in {None, "id"}:
                semantic = _infer_semantic_type_from_stable_plan(scenario_output, item.name)
            provided_keys.update(_normalize_context_key(alias) for alias in _provided_state_aliases(item.name, semantic))
        score = len(desired_keys & provided_keys)
        if score > 0 and scenario_output.scenario_path:
            scored.append((score, scenario_output.scenario_path))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _dependency_desired_keys(dependency: ScenarioDependency) -> set[str]:
    return {_normalize_context_key(item) for item in dependency.required_data}


def _setup_dependencies(state: ProjectState) -> list[ScenarioDependency]:
    """Return dependencies that require execution of a stable scenario package."""

    if not state.understanding:
        return []
    return [
        item
        for item in state.understanding.scenario_dependencies
        if item.kind == "requires_scenario"
        or (item.kind == "requires_state" and bool(item.required_data))
        or (item.kind == "requires_data" and bool(item.reference))
    ]


def _add_path_placeholders_to_dependencies(state: ProjectState) -> list[str]:
    if not state.understanding:
        return []
    placeholders = sorted(
        {
            match.group(1)
            for mention in [*state.scenario.raw_endpoint_mentions, *state.understanding.endpoint_mentions]
            for match in re.finditer(r"\{([A-Za-z0-9_]+)\}", mention.path)
            if match.group(1)
        }
    )
    if not placeholders:
        return []

    added: list[str] = []
    for dependency in state.understanding.scenario_dependencies:
        if dependency.kind not in {"requires_scenario", "requires_state"}:
            continue
        existing = {_normalize_context_key(item) for item in dependency.required_data}
        for placeholder in placeholders:
            if _normalize_context_key(placeholder) not in existing:
                dependency.required_data.append(placeholder)
                existing.add(_normalize_context_key(placeholder))
                added.append(placeholder)
    return added


def _dependency_reference_matches_file(reference: str, item: Path) -> bool:
    reference_norm = reference.casefold()
    stem_norm = item.stem.casefold()
    if stem_norm == reference_norm or reference_norm in stem_norm:
        return True

    number = _extract_scenario_number(reference)
    if number is None:
        return False
    return item.stem.startswith(f"{number:02d}-") or item.stem.startswith(f"{number}-")


def _extract_scenario_number(reference: str) -> int | None:
    match = re.search(r"(?:scenario|сценарий|постановка)\s*[-№#:]?\s*(\d+)", reference, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _context_from_setup(package_dir: str | Path, variables: dict[str, Any]) -> dict[str, Any]:
    context = dict(variables)
    scenario_output = load_scenario_output(package_dir)
    for item in scenario_output.provided_state:
        if item.name in variables:
            value = variables[item.name]
            context[item.name] = value
            inferred_semantic_type = item.semantic_type
            if inferred_semantic_type in {None, "id"}:
                inferred_semantic_type = _infer_semantic_type_from_stable_plan(scenario_output, item.name)
            for alias in _provided_state_aliases(item.name, inferred_semantic_type):
                context.setdefault(alias, value)
    return context


def _stable_setup_step_limit(package_dir: str | Path, dependency: ScenarioDependency) -> int | None:
    required_keys = {_normalize_context_key(item) for item in dependency.required_data}
    scenario_output = load_scenario_output(package_dir)
    if not required_keys:
        return _default_setup_checkpoint(scenario_output)

    matched_steps = []
    for item in scenario_output.provided_state:
        inferred_semantic_type = item.semantic_type
        if inferred_semantic_type in {None, "id"}:
            inferred_semantic_type = _infer_semantic_type_from_stable_plan(scenario_output, item.name)
        aliases = set(_provided_state_aliases(item.name, inferred_semantic_type))
        if aliases & required_keys and item.source_step_id:
            step_number = _step_number(item.source_step_id)
            if step_number:
                matched_steps.append(step_number)

    return max(matched_steps) if matched_steps else None


def _default_setup_checkpoint(scenario_output) -> int | None:
    if not scenario_output.stable_plan:
        return None
    for index, step in enumerate(scenario_output.stable_plan.steps, start=1):
        if step.operation.method.upper() != "POST" or "{" in step.operation.path:
            continue
        for extraction in step.response_extractions:
            if extraction.json_path == "$.id":
                return index
    return None


def _step_number(step_id: str) -> int | None:
    match = re.fullmatch(r"s0*(\d+)", step_id)
    return int(match.group(1)) if match else None


def _infer_semantic_type_from_stable_plan(scenario_output, variable: str) -> str | None:
    if not scenario_output.stable_plan:
        return None
    for index, step in enumerate(scenario_output.stable_plan.steps, start=1):
        step_id = f"s{index:02d}"
        for extraction in step.response_extractions:
            if extraction.variable == variable:
                return semantic_type(
                    variable,
                    operation_path=step.operation.path,
                    json_path=extraction.json_path,
                )
    return None


def _provided_state_aliases(name: str, semantic_type: str | None) -> list[str]:
    aliases = []
    for value in [semantic_type, name]:
        if not value:
            continue
        normalized = _normalize_context_key(value)
        if normalized and normalized not in aliases:
            aliases.append(normalized)
        singular = _singular_id_alias(normalized)
        if singular and singular not in aliases:
            aliases.append(singular)
    return aliases


def _normalize_context_key(value: str) -> str:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^A-Za-z0-9]+", "_", expanded).strip("_").lower()


def _singular_id_alias(value: str) -> str | None:
    if not value.endswith("_id"):
        return None
    prefix = value[:-3]
    if prefix.endswith("ies") and len(prefix) > 3:
        return f"{prefix[:-3]}y_id"
    if prefix.endswith("s") and len(prefix) > 1:
        return f"{prefix[:-1]}_id"
    return None


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
