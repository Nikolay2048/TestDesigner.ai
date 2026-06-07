from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

from agents import (
    DependencyResolverAgent,
    DocumentationAnalystAgent,
    EndpointMapperAgent,
    GenerationBindingAgent,
    StabilizationDiagnosticianAgent,
    StabilizationFixerAgent,
    TestDesignerAgent,
)
from data_dependencies import (
    assemble_data_binding_plan,
    build_external_context_binding_decisions,
    build_dependency_graph,
    build_dependency_resolution_tasks,
    build_generation_binding_tasks,
    build_static_test_data_binding_decisions,
    complete_generation_bindings_with_fallbacks,
)
from dependency_context import apply_dependency_context_to_endpoint_mapping
from domain import (
    AgentRun,
    DependencyResolverResult,
    EndpointMappingResult,
    GenerationBindingResult,
    ScenarioRunOutput,
    StabilizationAttempt,
    StabilizationFix,
    StabilizationResult,
    ProjectState,
)
from executor import FlowExecutor
from generators import GeneratorRegistry
from io_utils import ArtifactStore, load_scenario, load_test_data
from llm import LLM
from openapi import load_openapi_operations
from patches import apply_binding_patch
from postman_export import export_postman_artifacts
from stable import build_provided_state, publish_stable_package
from stabilization_rules import patch_from_server_hint
from test_design import execute_test_cases
from validators import (
    order_endpoint_mapping_by_business_steps,
    validate_data_binding,
    validate_endpoint_mapping,
)


class AgenticTestDesignOrchestrator:
    """Runs the currently implemented learning stage."""

    def __init__(self, llm: LLM | None = None):
        self.generator_registry = GeneratorRegistry()
        self.documentation_analyst = DocumentationAnalystAgent(llm)
        self.endpoint_mapper = EndpointMapperAgent(llm)
        self.test_designer = TestDesignerAgent(llm)
        self.llm = llm

    def run(
        self,
        scenario_path: str,
        openapi_path: str,
        out_dir: str | Path,
        test_data_path: str | None = None,
        base_url: str = "http://localhost:8080",
        max_attempts: int = 7,
        max_fixer_tries: int = 7,
        external_context: dict | None = None,
        external_context_factory=None,
        dependency_setup_plan=None,
        stable_dir: str | Path | None = None,
        publish_stable: bool = True,
        reset_log: bool = True,
        run_test_cases: bool = False,
        export_postman: bool = False,
    ) -> ProjectState:
        state = ProjectState(
            scenario=load_scenario(scenario_path),
            operations=load_openapi_operations(openapi_path),
            static_test_data=load_test_data(test_data_path),
            external_context=external_context or {},
            dependency_setup_plan=dependency_setup_plan,
        )
        store = ArtifactStore(out_dir)
        if reset_log:
            store.reset_log()
        store.log_event(
            "Run started",
            scenario=scenario_path,
            openapi=openapi_path,
            base_url=base_url,
            max_attempts=max_attempts,
            max_fixer_tries=max_fixer_tries,
        )
        store.log_event(
            "Inputs loaded",
            operations=len(state.operations),
            static_keys=",".join(sorted(state.static_test_data.keys())) or "-",
            external_keys=",".join(sorted(state.external_context.keys())) or "-",
        )

        # Stage 1: understand the human-written scenario.
        store.log_event("Stage started", stage="documentation_analyst")
        state, run = self.documentation_analyst.run(state)
        state.agent_runs.append(run)
        store.save_agent_run(run)
        store.log_event("Stage finished", stage="documentation_analyst", status=run.status)
        if run.status != "completed":
            store.save_state(state)
            store.log_event("Run stopped", reason="documentation_analyst_failed")
            return state

        # Stage 2: map extracted business steps to available OpenAPI operations.
        store.log_event("Stage started", stage="endpoint_mapper")
        state, run = self._run_endpoint_mapper_tasks(state, store)
        if state.endpoint_mapping:
            state.endpoint_mapping = validate_endpoint_mapping(state.endpoint_mapping, state.operations)
            if state.understanding:
                state.endpoint_mapping = order_endpoint_mapping_by_business_steps(
                    state.endpoint_mapping,
                    state.understanding.business_steps,
                )
            dependency_notes = apply_dependency_context_to_endpoint_mapping(state)
            if run.output is not None:
                run.output = state.endpoint_mapping.model_dump(mode="json")
            for note in dependency_notes:
                run.notes.append(note)
        state.agent_runs.append(run)
        store.save_agent_run(run)
        store.log_event(
            "Stage finished",
            stage="endpoint_mapper",
            status=run.status,
            mappings=len(state.endpoint_mapping.mappings) if state.endpoint_mapping else 0,
        )
        if run.status != "completed":
            store.save_state(state)
            store.log_event("Run stopped", reason="endpoint_mapper_failed")
            return state
        mapped_operation_count = sum(
            len(item.operations)
            for item in (state.endpoint_mapping.mappings if state.endpoint_mapping else [])
        )
        if mapped_operation_count == 0:
            run.status = "failed"
            run.notes.append("No valid REST operation was mapped for the scenario.")
            state.agent_runs[-1] = run
            store.save_agent_run(run)
            store.save_state(state)
            store.log_event("Run stopped", reason="endpoint_mapper_produced_no_operations")
            return state

        # Stage 3: extract request needs and response producers deterministically.
        store.log_event("Stage started", stage="data_dependency_graph")
        state.data_dependency_graph = build_dependency_graph(state)
        dependency_tasks = build_dependency_resolution_tasks(state.data_dependency_graph)
        store.log_event(
            "Stage finished",
            stage="data_dependency_graph",
            steps=len(state.data_dependency_graph.steps) if state.data_dependency_graph else 0,
            dependency_tasks=len(dependency_tasks),
        )

        # Stage 4: choose previous response candidates for request fields.
        store.log_event("Stage started", stage="dependency_resolver", tasks=len(dependency_tasks))
        state, run = self._run_dependency_resolver_tasks(state, store, dependency_tasks)
        state.agent_runs.append(run)
        store.save_agent_run(run)
        store.log_event("Stage finished", stage="dependency_resolver", status=run.status)
        if run.status != "completed":
            store.save_state(state)
            store.log_event("Run stopped", reason="dependency_resolver_failed")
            return state

        # Stage 5: choose static/generated/computed/literal sources for remaining fields.
        external_decisions = build_external_context_binding_decisions(
            state.data_dependency_graph,
            state.dependency_resolutions.resolutions if state.dependency_resolutions else [],
            state,
        )
        if external_decisions:
            store.log_event(
                "External context bindings selected",
                count=len(external_decisions),
                targets=",".join(f"{item.step_id}:{item.target}" for item in external_decisions),
            )
        static_decisions = build_static_test_data_binding_decisions(
            state.data_dependency_graph,
            state.dependency_resolutions.resolutions if state.dependency_resolutions else [],
            external_decisions,
            state,
        )
        if static_decisions:
            store.log_event(
                "Static test data bindings selected",
                count=len(static_decisions),
                targets=",".join(f"{item.step_id}:{item.target}" for item in static_decisions),
            )
        deterministic_decisions = [*external_decisions, *static_decisions]
        generation_tasks = build_generation_binding_tasks(
            state.data_dependency_graph,
            state.dependency_resolutions.resolutions if state.dependency_resolutions else [],
            state,
            self.generator_registry,
            existing_decisions=deterministic_decisions,
        )
        store.log_event("Stage started", stage="generation_binding", tasks=len(generation_tasks))
        state, run = self._run_generation_binding_tasks(
            state,
            store,
            generation_tasks,
            initial_decisions=deterministic_decisions,
        )
        state.agent_runs.append(run)
        store.save_agent_run(run)
        store.log_event("Stage finished", stage="generation_binding", status=run.status)
        if run.status != "completed":
            store.save_state(state)
            store.log_event("Run stopped", reason="generation_binding_failed")
            return state
        if state.generation_bindings and state.data_dependency_graph:
            state.generation_bindings.decisions = complete_generation_bindings_with_fallbacks(
                state.data_dependency_graph,
                state.generation_bindings.decisions,
            )

        # Stage 6: assemble the final plan in deterministic code.
        store.log_event("Stage started", stage="data_binding_assembly")
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
                state.external_context,
            )
        store.log_event(
            "Stage finished",
            stage="data_binding_assembly",
            steps=len(state.data_binding.steps) if state.data_binding else 0,
            risks=len(state.data_binding.risks) if state.data_binding else 0,
        )

        state = self._run_stabilization_loop(
            state,
            store,
            base_url,
            max_attempts,
            max_fixer_tries,
            external_context_factory=external_context_factory,
        )
        if state.stabilization and state.stabilization.status == "passed":
            store.log_event("Stage started", stage="test_designer")
            state, run = self.test_designer.run(state)
            state.agent_runs.append(run)
            store.save_agent_run(run, "test_design/test_designer")
            if state.test_design:
                if run_test_cases and state.stabilization and state.stabilization.stable_plan:
                    store.log_event(
                        "Test case execution started",
                        cases=len(state.test_design.test_cases),
                    )
                    state.test_design.executions = execute_test_cases(
                        state.test_design,
                        state.stabilization.stable_plan,
                        base_url=base_url,
                        static_test_data=state.static_test_data,
                        external_context=state.external_context,
                        external_context_factory=external_context_factory,
                        generator_registry=self.generator_registry,
                    )
                    execution_summary = _test_case_execution_summary(state.test_design.executions)
                    store.log_event(
                        "Test case execution finished",
                        total=execution_summary["total"],
                        **execution_summary["statuses"],
                    )
                store.save_json("test_design/test_basis.json", state.test_design.basis.model_dump(mode="json"))
                store.save_json("test_design/test_ideas.json", [item.model_dump(mode="json") for item in state.test_design.ideas])
                store.save_json("test_design/test_cases.json", [item.model_dump(mode="json") for item in state.test_design.test_cases])
                store.save_json("test_design/test_case_executions.json", [item.model_dump(mode="json") for item in state.test_design.executions])
                store.save_json(
                    "test_design/test_case_execution_summary.json",
                    _test_case_execution_summary(state.test_design.executions),
                )
            store.log_event("Stage finished", stage="test_designer", status=run.status)
            if export_postman:
                store.log_event("Stage started", stage="postman_export")
                summary = export_postman_artifacts(
                    state,
                    out_dir=store.root,
                    base_url=base_url,
                    generator_registry=self.generator_registry,
                )
                store.log_event(
                    "Stage finished",
                    stage="postman_export",
                    happy_path_requests=summary["happy_path_requests"],
                    test_cases=summary["test_cases"],
                    test_case_requests=summary["test_case_requests"],
                    unsupported=len(summary["unsupported_features"]),
                    review=len(summary["requires_human_review"]),
                )
        self._save_scenario_output(state, store)
        if publish_stable and stable_dir:
            package_dir = publish_stable_package(
                state,
                stable_dir=stable_dir,
                openapi_path=openapi_path,
                test_data_path=test_data_path,
                base_url=base_url,
            )
            if package_dir:
                store.log_event("Stable package published", path=package_dir)
        store.save_state(state)
        store.log_event(
            "Run finished",
            stabilization_status=state.stabilization.status if state.stabilization else "not_started",
        )
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
            store.log_event(
                "Dependency resolver task started",
                step=task.step_id,
                target=task.need.target,
                candidates=len(task.candidates),
            )
            agent = DependencyResolverAgent(self.llm, tasks=[task])
            state, run = agent.run(state)
            artifact_name = f"dependency_resolver_tasks/{_task_artifact_name(task.step_id, task.need.target)}"
            store.save_agent_run(run, artifact_name)
            store.log_event(
                "Dependency resolver task finished",
                step=task.step_id,
                target=task.need.target,
                status=run.status,
            )
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

    def _run_endpoint_mapper_tasks(
        self,
        state: ProjectState,
        store: ArtifactStore,
    ) -> tuple[ProjectState, AgentRun]:
        steps = state.understanding.business_steps if state.understanding else []
        mappings = []
        unmapped = []
        risks = []
        status = "completed"
        notes = []
        for index, business_step in enumerate(steps, start=1):
            store.log_event(
                "Endpoint mapper task started",
                task=index,
                business_step=business_step,
            )
            task_state = state.model_copy(deep=True)
            agent = EndpointMapperAgent(self.llm, business_steps=[business_step])
            task_state, task_run = agent.run(task_state)
            store.save_agent_run(task_run, f"endpoint_mapper_tasks/task_{index:02d}")
            store.log_event(
                "Endpoint mapper task finished",
                task=index,
                status=task_run.status,
            )
            if task_run.status != "completed" or not task_state.endpoint_mapping:
                status = "failed"
                notes.extend(task_run.notes or [f"Endpoint mapping failed for: {business_step}"])
                continue
            mappings.extend(task_state.endpoint_mapping.mappings)
            unmapped.extend(task_state.endpoint_mapping.unmapped_steps)
            risks.extend(task_state.endpoint_mapping.risks)

        state.endpoint_mapping = EndpointMappingResult(
            mappings=mappings,
            unmapped_steps=unmapped,
            risks=risks,
        )
        summary = AgentRun(
            agent_name="Endpoint Mapper",
            status=status,
            output=state.endpoint_mapping.model_dump(mode="json"),
            notes=notes,
        )
        return state, summary

    def _run_generation_binding_tasks(
        self,
        state: ProjectState,
        store: ArtifactStore,
        tasks,
        initial_decisions=None,
    ) -> tuple[ProjectState, AgentRun]:
        decisions = list(initial_decisions or [])
        risks = []
        summary = AgentRun(agent_name="Generation Binding", status="completed")

        for task in tasks:
            store.log_event(
                "Generation binding task started",
                step=task.step_id,
                target=task.need.target,
                type=task.need.type,
            )
            agent = GenerationBindingAgent(self.llm, self.generator_registry, tasks=[task])
            state, run = agent.run(state)
            artifact_name = f"generation_binding_tasks/{_task_artifact_name(task.step_id, task.need.target)}"
            store.save_agent_run(run, artifact_name)
            store.log_event(
                "Generation binding task finished",
                step=task.step_id,
                target=task.need.target,
                status=run.status,
            )
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
        max_fixer_tries: int,
        external_context_factory=None,
    ) -> ProjectState:
        if not state.data_binding:
            store.log_event("Stabilization skipped", reason="missing_data_binding")
            return state

        state.stabilization = StabilizationResult()
        executor = FlowExecutor(
            base_url=base_url,
            static_test_data=state.static_test_data,
            external_context=state.external_context,
            generator_registry=self.generator_registry,
        )

        for attempt_number in range(1, max_attempts + 1):
            if external_context_factory:
                state.external_context = external_context_factory(attempt_number)
                executor.external_context = state.external_context
                store.log_event(
                    "External context refreshed",
                    attempt=attempt_number,
                    keys=",".join(sorted(state.external_context.keys())) or "-",
                )
            store.log_event("Executor attempt started", attempt=attempt_number)
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
            store.log_event(
                "Executor attempt finished",
                attempt=attempt_number,
                status=trace.status,
                failed_step=trace.failed_step_id or "-",
                failure=trace.failure or "-",
            )

            if trace.status == "passed":
                state.data_binding = validate_data_binding(
                    state.data_binding,
                    state.operations,
                    state.static_test_data,
                    self.generator_registry,
                    state.external_context,
                )
                state.stabilization.status = "passed"
                state.stabilization.stable_plan = state.data_binding
                store.log_event("Stabilization passed", attempt=attempt_number)
                return state

            store.log_event("Diagnosis started", attempt=attempt_number)
            diagnostician = StabilizationDiagnosticianAgent(self.llm)
            state, diagnosis_run = diagnostician.run(state)
            store.save_agent_run(diagnosis_run, f"stabilization_diagnosis/attempt_{attempt_number:02d}")
            store.log_event("Diagnosis finished", attempt=attempt_number, status=diagnosis_run.status)
            if diagnosis_run.status != "completed":
                state.stabilization.review_notes.append(
                    f"Diagnosis failed on attempt {attempt_number}: {diagnosis_run.notes}"
                )
                store.log_event("Stabilization stopped", reason="diagnosis_failed", attempt=attempt_number)
                return state

            diagnosis = diagnostician.output_model.model_validate(diagnosis_run.output)
            attempt.diagnosis = diagnosis
            store.log_event(
                "Diagnosis accepted",
                attempt=attempt_number,
                failed_step=diagnosis.failed_step_id,
                failure_type=diagnosis.failure_type,
                suspected=len(diagnosis.suspected_bindings),
            )

            applied = []
            deterministic_patch = patch_from_server_hint(state, diagnosis)
            if deterministic_patch:
                store.log_event(
                    "Deterministic patch proposed",
                    attempt=attempt_number,
                    patch_type=deterministic_patch.patch_type,
                    step=deterministic_patch.step_id or "-",
                    target=deterministic_patch.target or "-",
                )
                applied_patch = apply_binding_patch(
                    state.data_binding,
                    deterministic_patch,
                    state.static_test_data,
                    self.generator_registry,
                    allowed_bindings=diagnosis.suspected_bindings,
                    allowed_operations=state.operations,
                )
                if applied_patch:
                    applied.append(applied_patch)
                    attempt.fix = StabilizationFix(
                        attempt=attempt_number,
                        patches=[applied_patch],
                        reason=applied_patch.reason,
                    )
                    attempt.applied_patches = applied
                    if applied_patch.requires_human_review:
                        state.stabilization.review_required = True
                        state.stabilization.review_notes.append(applied_patch.reason)
                    store.log_event(
                        "Deterministic patch applied",
                        attempt=attempt_number,
                        patch_type=applied_patch.patch_type,
                        step=applied_patch.step_id or "-",
                        target=applied_patch.target or "-",
                    )
                    continue

            rejected_proposals: list[dict] = []
            seen_patch_signatures: set[str] = set()
            for fixer_try in range(1, max_fixer_tries + 1):
                store.log_event("Fixer try started", attempt=attempt_number, try_number=fixer_try)
                fixer = StabilizationFixerAgent(
                    self.llm,
                    diagnosis,
                    self.generator_registry,
                    fixer_try=fixer_try,
                    rejected_proposals=rejected_proposals,
                )
                state, fix_run = fixer.run(state)
                store.save_agent_run(
                    fix_run,
                    f"stabilization_fixes/attempt_{attempt_number:02d}_try_{fixer_try:02d}",
                )
                store.log_event(
                    "Fixer try finished",
                    attempt=attempt_number,
                    try_number=fixer_try,
                    status=fix_run.status,
                )
                if fix_run.status != "completed":
                    rejection_reason = (
                        f"Fixer output failed schema validation: {'; '.join(fix_run.notes)}"
                    )
                    state.stabilization.review_notes.append(
                        f"Fixer rejected on attempt {attempt_number}, try {fixer_try}: "
                        f"{rejection_reason}"
                    )
                    rejected_proposals.append(
                        {
                            "fixer_try": fixer_try,
                            "patch": fix_run.output,
                            "rejection_reason": rejection_reason,
                        }
                    )
                    store.log_event(
                        "Fixer output rejected",
                        attempt=attempt_number,
                        try_number=fixer_try,
                        reason=rejection_reason,
                    )
                    continue

                fix = fixer.output_model.model_validate(fix_run.output)
                attempt.fix = fix
                for patch in fix.patches[:1]:
                    patch_signature = patch.model_dump_json(exclude={"reason", "why_not_repeating_previous_fix"})
                    if patch_signature in seen_patch_signatures:
                        rejection_reason = "The same patch was already proposed and rejected for this diagnosis."
                        rejected_proposals.append(
                            {
                                "fixer_try": fixer_try,
                                "patch": patch.model_dump(mode="json"),
                                "rejection_reason": rejection_reason,
                            }
                        )
                        store.log_event(
                            "Patch rejected",
                            attempt=attempt_number,
                            try_number=fixer_try,
                            reason=rejection_reason,
                        )
                        continue
                    seen_patch_signatures.add(patch_signature)

                    if patch.patch_type == "no_patch" and patch.requires_human_review:
                        state.stabilization.review_required = True
                        state.stabilization.review_notes.append(patch.reason)
                        store.log_event(
                            "Human review requested",
                            attempt=attempt_number,
                            try_number=fixer_try,
                            step=patch.step_id or "-",
                            target=patch.target or patch.variable or "-",
                            reason=patch.reason,
                        )
                    store.log_event(
                        "Patch proposed",
                        attempt=attempt_number,
                        try_number=fixer_try,
                        patch_type=patch.patch_type,
                        step=patch.step_id or "-",
                        target=patch.target or patch.variable or "-",
                    )
                    try:
                        applied_patch = apply_binding_patch(
                            state.data_binding,
                            patch,
                            state.static_test_data,
                            self.generator_registry,
                            allowed_bindings=diagnosis.suspected_bindings,
                            allowed_operations=state.operations,
                        )
                    except Exception as exc:
                        rejection_reason = str(exc)
                        state.stabilization.review_notes.append(
                            f"Patch rejected on attempt {attempt_number}, try {fixer_try}: {rejection_reason}"
                        )
                        store.log_event(
                            "Patch rejected",
                            attempt=attempt_number,
                            try_number=fixer_try,
                            reason=rejection_reason,
                        )
                        applied_patch = None
                    if applied_patch:
                        applied.append(applied_patch)
                        store.log_event(
                            "Patch applied",
                            attempt=attempt_number,
                            try_number=fixer_try,
                            patch_type=applied_patch.patch_type,
                            step=applied_patch.step_id or "-",
                            target=applied_patch.target or applied_patch.variable or "-",
                        )
                        if applied_patch.requires_human_review:
                            state.stabilization.review_required = True
                            state.stabilization.review_notes.append(applied_patch.reason)
                    else:
                        rejection_reason = (
                            "Deterministic validator rejected the patch. The target may not exist, "
                            "the patch type may not match the current binding, or the referenced "
                            "variable/source may be unavailable."
                        )
                        rejected_proposals.append(
                            {
                                "fixer_try": fixer_try,
                                "patch": patch.model_dump(mode="json"),
                                "rejection_reason": rejection_reason,
                            }
                        )

                if applied:
                    break

                state.stabilization.review_notes.append(
                    f"No valid patch from fixer on attempt {attempt_number}, try {fixer_try}."
                )
                store.log_event("No valid patch from fixer", attempt=attempt_number, try_number=fixer_try)

            attempt.applied_patches = applied
            if not applied:
                state.stabilization.review_notes.append(
                    f"No patch applied on attempt {attempt_number}; stopping stabilization."
                )
                store.log_event("Stabilization stopped", reason="no_patch_applied", attempt=attempt_number)
                return state

        state.stabilization.status = "failed"
        state.stabilization.review_notes.append(f"Reached max_attempts={max_attempts}.")
        store.log_event("Stabilization failed", reason="max_attempts_reached", max_attempts=max_attempts)
        return state


    def _save_scenario_output(self, state: ProjectState, store: ArtifactStore) -> None:
        status = state.stabilization.status if state.stabilization else "failed"
        output = ScenarioRunOutput(
            scenario_path=state.scenario.path,
            status=status,
            provided_state=build_provided_state(state),
            stable_plan=state.stabilization.stable_plan if state.stabilization else None,
        )
        store.save_json("provided_state.json", output.model_dump(mode="json"))
        store.log_event(
            "Scenario output saved",
            status=output.status,
            provided_state=len(output.provided_state),
        )


def _task_artifact_name(step_id: str, target: str) -> str:
    safe_target = re.sub(r"[^A-Za-z0-9]+", "_", target).strip("_") or "value"
    return f"{step_id}_{safe_target}"


def _test_case_execution_summary(executions) -> dict:
    statuses = Counter(item.status for item in executions)
    ordered_statuses = {
        status: statuses[status]
        for status in [
            "passed",
            "failed",
            "review_required",
            "blocked",
            "contract_mismatch",
            "oracle_incomplete",
            "weak_attack",
            "not_run",
        ]
        if statuses[status]
    }
    return {
        "total": len(executions),
        "statuses": ordered_statuses,
    }
