from __future__ import annotations

from pathlib import Path
import re

from agents import (
    DependencyResolverAgent,
    DocumentationAnalystAgent,
    EndpointMapperAgent,
    GenerationBindingAgent,
    StabilizationDiagnosticianAgent,
    StabilizationFixerAgent,
)
from data_dependencies import (
    assemble_data_binding_plan,
    build_dependency_graph,
    build_dependency_resolution_tasks,
    build_generation_binding_tasks,
)
from domain import (
    AgentRun,
    DependencyResolverResult,
    GenerationBindingResult,
    StabilizationAttempt,
    StabilizationResult,
    ProjectState,
)
from executor import FlowExecutor
from generators import GeneratorRegistry
from io_utils import ArtifactStore, load_scenario, load_test_data
from llm import LLM
from openapi import load_openapi_operations
from patches import apply_binding_patch
from validators import validate_data_binding, validate_endpoint_mapping


class AgenticTestDesignOrchestrator:
    """Runs the currently implemented learning stage."""

    def __init__(self, llm: LLM | None = None):
        self.generator_registry = GeneratorRegistry()
        self.documentation_analyst = DocumentationAnalystAgent(llm)
        self.endpoint_mapper = EndpointMapperAgent(llm)
        self.llm = llm

    def run(
        self,
        scenario_path: str,
        openapi_path: str,
        out_dir: str | Path,
        test_data_path: str | None = None,
        base_url: str = "http://localhost:8080",
        max_attempts: int = 7,
    ) -> ProjectState:
        state = ProjectState(
            scenario=load_scenario(scenario_path),
            operations=load_openapi_operations(openapi_path),
            static_test_data=load_test_data(test_data_path),
        )
        store = ArtifactStore(out_dir)

        # Stage 1: understand the human-written scenario.
        state, run = self.documentation_analyst.run(state)
        state.agent_runs.append(run)
        store.save_agent_run(run)
        if run.status != "completed":
            store.save_state(state)
            return state

        # Stage 2: map extracted business steps to available OpenAPI operations.
        state, run = self.endpoint_mapper.run(state)
        if state.endpoint_mapping:
            state.endpoint_mapping = validate_endpoint_mapping(state.endpoint_mapping, state.operations)
            if run.output is not None:
                run.output = state.endpoint_mapping.model_dump(mode="json")
        state.agent_runs.append(run)
        store.save_agent_run(run)
        if run.status != "completed":
            store.save_state(state)
            return state

        # Stage 3: extract request needs and response producers deterministically.
        state.data_dependency_graph = build_dependency_graph(state)
        dependency_tasks = build_dependency_resolution_tasks(state.data_dependency_graph)

        # Stage 4: choose previous response candidates for request fields.
        state, run = self._run_dependency_resolver_tasks(state, store, dependency_tasks)
        state.agent_runs.append(run)
        store.save_agent_run(run)
        if run.status != "completed":
            store.save_state(state)
            return state

        # Stage 5: choose static/generated/computed/literal sources for remaining fields.
        generation_tasks = build_generation_binding_tasks(
            state.data_dependency_graph,
            state.dependency_resolutions.resolutions if state.dependency_resolutions else [],
            state,
            self.generator_registry,
        )
        state, run = self._run_generation_binding_tasks(state, store, generation_tasks)
        state.agent_runs.append(run)
        store.save_agent_run(run)
        if run.status != "completed":
            store.save_state(state)
            return state

        # Stage 6: assemble the final plan in deterministic code.
        state.data_binding = assemble_data_binding_plan(
            state.data_dependency_graph,
            dependency_tasks,
            state.dependency_resolutions.resolutions if state.dependency_resolutions else [],
            state.generation_bindings.decisions if state.generation_bindings else [],
        )
        if state.data_binding:
            state.data_binding = validate_data_binding(
                state.data_binding,
                state.operations,
                state.static_test_data,
                self.generator_registry,
            )

        state = self._run_stabilization_loop(state, store, base_url, max_attempts)
        store.save_state(state)
        return state

    def _run_dependency_resolver_tasks(
        self,
        state: ProjectState,
        store: ArtifactStore,
        tasks,
    ) -> tuple[ProjectState, AgentRun]:
        resolutions = []
        risks = []
        summary = AgentRun(agent_name="Dependency Resolver", status="completed")

        for task in tasks:
            agent = DependencyResolverAgent(self.llm, tasks=[task])
            state, run = agent.run(state)
            artifact_name = f"dependency_resolver_tasks/{_task_artifact_name(task.step_id, task.need.target)}"
            store.save_agent_run(run, artifact_name)
            if run.status != "completed":
                summary.status = run.status
                summary.notes.append(f"{task.step_id} {task.need.target}: {run.status}")
                summary.output = {"failed_task": task.model_dump(mode="json")}
                return state, summary
            if state.dependency_resolutions:
                resolutions.extend(state.dependency_resolutions.resolutions)
                risks.extend(state.dependency_resolutions.risks)

        state.dependency_resolutions = DependencyResolverResult(
            resolutions=resolutions,
            risks=risks,
        )
        summary.output = state.dependency_resolutions.model_dump(mode="json")
        summary.notes.append(f"Resolved {len(resolutions)} dependency tasks.")
        return state, summary

    def _run_generation_binding_tasks(
        self,
        state: ProjectState,
        store: ArtifactStore,
        tasks,
    ) -> tuple[ProjectState, AgentRun]:
        decisions = []
        risks = []
        summary = AgentRun(agent_name="Generation Binding", status="completed")

        for task in tasks:
            agent = GenerationBindingAgent(self.llm, self.generator_registry, tasks=[task])
            state, run = agent.run(state)
            artifact_name = f"generation_binding_tasks/{_task_artifact_name(task.step_id, task.need.target)}"
            store.save_agent_run(run, artifact_name)
            if run.status != "completed":
                summary.status = run.status
                summary.notes.append(f"{task.step_id} {task.need.target}: {run.status}")
                summary.output = {"failed_task": task.model_dump(mode="json")}
                return state, summary
            if state.generation_bindings:
                decisions.extend(state.generation_bindings.decisions)
                risks.extend(state.generation_bindings.risks)

        state.generation_bindings = GenerationBindingResult(
            decisions=decisions,
            risks=risks,
        )
        summary.output = state.generation_bindings.model_dump(mode="json")
        summary.notes.append(f"Bound {len(decisions)} generation tasks.")
        return state, summary

    def _run_stabilization_loop(
        self,
        state: ProjectState,
        store: ArtifactStore,
        base_url: str,
        max_attempts: int,
    ) -> ProjectState:
        if not state.data_binding:
            return state

        state.stabilization = StabilizationResult()
        executor = FlowExecutor(
            base_url=base_url,
            static_test_data=state.static_test_data,
            generator_registry=self.generator_registry,
        )

        for attempt_number in range(1, max_attempts + 1):
            trace = executor.execute(state.data_binding, attempt=attempt_number)
            attempt = StabilizationAttempt(attempt=attempt_number, trace=trace)
            state.stabilization.attempts.append(attempt)
            store.save_state(state)
            store.save_agent_run(
                AgentRun(
                    agent_name="Flow Executor",
                    status="completed" if trace.status == "passed" else "failed",
                    output=trace.model_dump(mode="json"),
                ),
                f"executor_attempts/attempt_{attempt_number:02d}",
            )

            if trace.status == "passed":
                state.stabilization.status = "passed"
                state.stabilization.stable_plan = state.data_binding
                return state

            diagnostician = StabilizationDiagnosticianAgent(self.llm)
            state, diagnosis_run = diagnostician.run(state)
            store.save_agent_run(diagnosis_run, f"stabilization_diagnosis/attempt_{attempt_number:02d}")
            if diagnosis_run.status != "completed":
                state.stabilization.review_notes.append(
                    f"Diagnosis failed on attempt {attempt_number}: {diagnosis_run.notes}"
                )
                return state

            diagnosis = diagnostician.output_model.model_validate(diagnosis_run.output)
            attempt.diagnosis = diagnosis

            fixer = StabilizationFixerAgent(self.llm, diagnosis, self.generator_registry)
            state, fix_run = fixer.run(state)
            store.save_agent_run(fix_run, f"stabilization_fixes/attempt_{attempt_number:02d}")
            if fix_run.status != "completed":
                state.stabilization.review_notes.append(
                    f"Fixer failed on attempt {attempt_number}: {fix_run.notes}"
                )
                return state

            fix = fixer.output_model.model_validate(fix_run.output)
            attempt.fix = fix
            applied = []
            for patch in fix.patches[:1]:
                try:
                    applied_patch = apply_binding_patch(
                        state.data_binding,
                        patch,
                        state.static_test_data,
                        self.generator_registry,
                    )
                except Exception as exc:
                    state.stabilization.review_notes.append(
                        f"Patch rejected on attempt {attempt_number}: {exc}"
                    )
                    applied_patch = None
                if applied_patch:
                    applied.append(applied_patch)
                    if applied_patch.requires_human_review:
                        state.stabilization.review_required = True
                        state.stabilization.review_notes.append(applied_patch.reason)

            attempt.applied_patches = applied
            if not applied:
                state.stabilization.review_notes.append(
                    f"No patch applied on attempt {attempt_number}; stopping stabilization."
                )
                return state

        state.stabilization.status = "failed"
        state.stabilization.review_notes.append(f"Reached max_attempts={max_attempts}.")
        return state


def _task_artifact_name(step_id: str, target: str) -> str:
    safe_target = re.sub(r"[^A-Za-z0-9]+", "_", target).strip("_") or "value"
    return f"{step_id}_{safe_target}"
