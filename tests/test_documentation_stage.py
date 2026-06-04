from __future__ import annotations

import json
from pathlib import Path

from agents.dependency_resolver import DependencyResolverAgent
from agents.documentation_analyst import DocumentationAnalystAgent
from agents.endpoint_mapper import EndpointMapperAgent
from agents.generation_binding import GenerationBindingAgent
from agents.stabilization_fixer import StabilizationFixerAgent
from data_dependencies import (
    assemble_data_binding_plan,
    build_dependency_graph,
    build_dependency_resolution_tasks,
    build_external_context_binding_decisions,
    build_generation_binding_tasks,
    build_static_test_data_binding_decisions,
)
from dependency_context import apply_dependency_context_to_endpoint_mapping
from domain import (
    AgentRun,
    ApiOperation,
    BindingPatch,
    DataBindingPlan,
    DataDependencyGraph,
    DataDependencyStep,
    DataNeed,
    DataProducer,
    DependencyResolverResult,
    DependencyResolution,
    EndpointMappingResult,
    GenerationBindingDecision,
    OperationRef,
    ProjectState,
    RequestValueBinding,
    ExecutorStepTrace,
    ExecutorTrace,
    ResponseExtraction,
    ScenarioInput,
    ScenarioDependency,
    ScenarioRunOutput,
    ScenarioUnderstanding,
    StabilizationAttempt,
    StabilizationDiagnosis,
    StabilizationResult,
    StepDataBinding,
    StepOperationMapping,
    ProvidedState,
)
from executor import FlowExecutor
from generators import GeneratorRegistry
from io_utils import extract_raw_endpoint_mentions
from openapi import load_openapi_operations
from orchestrator import AgenticTestDesignOrchestrator
from patches import apply_binding_patch
from scenario_dependencies import (
    ScenarioDependencyRunner,
    _context_from_setup,
    _resolve_dependency_path,
    _stable_setup_step_limit,
)
from stabilization_rules import patch_from_server_hint
from stable import publish_stable_package, stable_package_dir, validate_stable_package
from validators import validate_data_binding, validate_endpoint_mapping


def test_raw_endpoint_mentions_are_extracted_deterministically() -> None:
    text = """
Available endpoints:
GET /locations
Use /reservations/{reservationId}/cancel later.
"""

    mentions = extract_raw_endpoint_mentions(text)

    assert [(item.method, item.path) for item in mentions] == [
        ("GET", "/locations"),
        (None, "/reservations/{reservationId}/cancel"),
    ]


def test_documentation_analyst_prompt_mentions_endpoint_capture() -> None:
    state = ProjectState(
        scenario=ScenarioInput(
            path="scenario.md",
            title="Demo",
            text="Step 1: call GET /locations",
            raw_endpoint_mentions=extract_raw_endpoint_mentions("Step 1: call GET /locations"),
        )
    )

    prompt = DocumentationAnalystAgent().build_prompt(state)

    assert "endpoint_mentions" in prompt[1].content
    assert '"path": "/locations"' in prompt[1].content
    assert "Do not add, remove, rewrite, normalize, or infer endpoints" in prompt[1].content
    assert "header|step|rule|unknown" in prompt[1].content
    assert "scenario_dependencies" in prompt[1].content
    assert "requires_scenario|requires_state|requires_data" in prompt[1].content
    assert "preserve the main action of each step" in prompt[1].content
    assert "vehicle pickup/start rental" in prompt[1].content


def test_orchestrator_without_llm_saves_documentation_prompt() -> None:
    out_dir = "runs/test_documentation_stage"

    state = AgenticTestDesignOrchestrator().run(
        scenario_path="data/carsharing/specs/01-basic-economy-rental.md",
        openapi_path="data/carsharing/openapi/openapi.yaml",
        out_dir=out_dir,
    )

    assert state.agent_runs[0].agent_name == "Documentation Analyst"
    assert state.agent_runs[0].status == "needs_llm"
    assert Path(out_dir, "documentation_analyst.prompt.md").exists()
    assert Path(out_dir, "documentation_analyst.run.json").exists()
    assert Path(out_dir, "state.json").exists()
    run_log = Path(out_dir, "run.log")
    assert run_log.exists()
    assert "Run started" in run_log.read_text(encoding="utf-8")
    assert "documentation_analyst_failed" in run_log.read_text(encoding="utf-8")


def test_endpoint_mapper_prompt_uses_compact_operations() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Demo", text="Demo"),
        operations=load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
        understanding=ScenarioUnderstanding(
            title="Demo",
            business_steps=["User searches available cars"],
        ),
    )

    prompt = EndpointMapperAgent().build_prompt(state)

    assert "Available OpenAPI operations" in prompt[1].content
    assert "Do not invent endpoints" in prompt[1].content
    assert "Do not put a step into unmapped_steps just because matching is hard" in prompt[1].content
    assert "Preserve lifecycle API steps that create IDs needed later" in prompt[1].content
    assert "vehicle pickup/start rental" in prompt[1].content
    assert "request_schema" not in prompt[1].content


def test_endpoint_mapping_validator_removes_invented_operations() -> None:
    mapping = EndpointMappingResult(
        mappings=[
            StepOperationMapping(
                business_step="Create booking",
                operations=[OperationRef(method="POST", path="/invented")],
            )
        ]
    )

    validated = validate_endpoint_mapping(
        mapping,
        load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
    )

    assert validated.mappings[0].operations == []
    assert validated.unmapped_steps[0].business_step == "Create booking"
    assert validated.unmapped_steps[0].reason
    assert "absent from OpenAPI" in validated.risks[0]


def test_endpoint_mapping_accepts_none_confidence_for_unmapped_steps() -> None:
    mapping = EndpointMappingResult(
        mappings=[
            StepOperationMapping(
                business_step="System closes rental",
                operations=[],
                source="none",
                confidence="none",
                reason="Expected outcome of return operation, no separate endpoint.",
            )
        ]
    )

    validated = validate_endpoint_mapping(
        mapping,
        load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
    )

    assert validated.unmapped_steps[0].business_step == "System closes rental"


def test_data_dependency_graph_extracts_needs_and_producers() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Demo", text="Demo"),
        operations=load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
        endpoint_mapping=EndpointMappingResult(
            mappings=[
                StepOperationMapping(
                    business_step="List locations",
                    operations=[OperationRef(method="GET", path="/locations")],
                ),
                StepOperationMapping(
                    business_step="Create reservation",
                    operations=[OperationRef(method="POST", path="/reservations")],
                )
            ]
        ),
    )

    graph = build_dependency_graph(state)

    assert graph.steps[0].produces[0].json_path == "$.locations[].id"
    assert "$.vehicleId" in [need.target for need in graph.steps[1].needs]
    assert "$.customer.phone" in [need.target for need in graph.steps[1].needs]


def test_data_dependency_graph_uses_path_namespace_for_path_params() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Demo", text="Demo"),
        operations=load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
        endpoint_mapping=EndpointMappingResult(
            mappings=[
                StepOperationMapping(
                    business_step="Open vehicle card",
                    operations=[OperationRef(method="GET", path="/vehicles/{vehicleId}")],
                )
            ]
        ),
    )

    graph = build_dependency_graph(state)

    assert graph.steps[0].needs[0].target == "$.path.vehicleId"
    assert graph.steps[0].needs[0].location == "path"


def test_dependency_resolver_prompt_uses_candidate_tasks() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Demo", text="Demo"),
        operations=load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
        endpoint_mapping=EndpointMappingResult(
            mappings=[
                StepOperationMapping(
                    business_step="List locations",
                    operations=[OperationRef(method="GET", path="/locations")],
                ),
                StepOperationMapping(
                    business_step="Search vehicles",
                    operations=[OperationRef(method="POST", path="/vehicles/search")],
                ),
            ]
        ),
    )
    state.data_dependency_graph = build_dependency_graph(state)

    prompt = DependencyResolverAgent().build_prompt(state)

    assert "Dependency resolution tasks" in prompt[1].content
    assert "$.pickupLocationId" in prompt[1].content
    assert "$.locations[].id" in prompt[1].content
    assert "Do not create new variables" in prompt[1].content
    assert "target must exactly match task.need.target" in prompt[1].content


def test_dependency_resolver_normalizes_path_param_target_from_task() -> None:
    task = build_dependency_resolution_tasks(
        DataDependencyGraph(
            steps=[
                DataDependencyStep(
                    step_id="s04",
                    business_step="Create reservation",
                    operation=OperationRef(method="POST", path="/reservations"),
                    produces=[
                        DataProducer(
                            step_id="s04",
                            json_path="$.id",
                            type="string",
                            operation=OperationRef(method="POST", path="/reservations"),
                        )
                    ],
                ),
                DataDependencyStep(
                    step_id="s05",
                    business_step="Open reservation",
                    operation=OperationRef(method="GET", path="/reservations/{reservationId}"),
                    needs=[
                        DataNeed(
                            step_id="s05",
                            target="$.path.reservationId",
                            location="path",
                            type="string",
                        )
                    ],
                ),
            ]
        )
    )[0]
    state = ProjectState(scenario=ScenarioInput(path="scenario.md", title="Demo", text="Demo"))
    output = DependencyResolverResult(
        resolutions=[
            DependencyResolution(
                step_id="s05",
                target="$.reservationId",
                selected_candidate_id=task.candidates[0].candidate_id,
                confidence="high",
            )
        ]
    )

    state = DependencyResolverAgent(tasks=[task]).apply_output(state, output)

    resolution = state.dependency_resolutions.resolutions[0]
    assert resolution.target == "$.path.reservationId"
    assert resolution.selected_candidate_id == "c_s04_id"


def test_dependency_candidates_filter_unrelated_same_type_fields() -> None:
    graph = DataDependencyGraph(
        steps=[
            DataDependencyStep(
                step_id="s03",
                business_step="Apply loyalty",
                operation=OperationRef(method="POST", path="/loyalty/validate"),
                produces=[
                    DataProducer(
                        step_id="s03",
                        json_path="$.discountPercent",
                        type="integer",
                        field_name="discountPercent",
                        operation=OperationRef(method="POST", path="/loyalty/validate"),
                    )
                ],
            ),
            DataDependencyStep(
                step_id="s04",
                business_step="Pickup",
                operation=OperationRef(method="POST", path="/rentals/{reservationId}/pickup"),
                needs=[
                    DataNeed(
                        step_id="s04",
                        target="$.odometer",
                        location="body",
                        type="integer",
                    ),
                    DataNeed(
                        step_id="s04",
                        target="$.fuelLevelPercent",
                        location="body",
                        type="integer",
                    ),
                ],
            ),
        ]
    )

    tasks = build_dependency_resolution_tasks(graph)

    assert tasks == []


def test_dependency_candidates_keep_resource_id_from_create_response() -> None:
    graph = DataDependencyGraph(
        steps=[
            DataDependencyStep(
                step_id="s01",
                business_step="Create reservation",
                operation=OperationRef(method="POST", path="/reservations"),
                produces=[
                    DataProducer(
                        step_id="s01",
                        json_path="$.id",
                        type="string",
                        field_name="id",
                        operation=OperationRef(method="POST", path="/reservations"),
                    )
                ],
            ),
            DataDependencyStep(
                step_id="s02",
                business_step="Open reservation",
                operation=OperationRef(method="GET", path="/reservations/{reservationId}"),
                needs=[
                    DataNeed(
                        step_id="s02",
                        target="$.path.reservationId",
                        location="path",
                        type="string",
                    )
                ],
            ),
        ]
    )

    tasks = build_dependency_resolution_tasks(graph)

    assert len(tasks) == 1
    assert tasks[0].candidates[0].candidate_id == "c_s01_id"


def test_generation_binding_prompt_uses_unresolved_fields() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Demo", text="Demo"),
        operations=load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
        static_test_data={"country_code": "RU"},
        endpoint_mapping=EndpointMappingResult(
            mappings=[
                StepOperationMapping(
                    business_step="List locations",
                    operations=[OperationRef(method="GET", path="/locations")],
                ),
                StepOperationMapping(
                    business_step="Search vehicles",
                    operations=[OperationRef(method="POST", path="/vehicles/search")],
                ),
            ]
        ),
    )
    state.data_dependency_graph = build_dependency_graph(state)
    tasks = build_dependency_resolution_tasks(state.data_dependency_graph)
    first_task = tasks[0]
    state.dependency_resolutions = DependencyResolverResult(
        resolutions=[
            DependencyResolution(
                step_id=first_task.step_id,
                target=first_task.need.target,
                selected_candidate_id=first_task.candidates[0].candidate_id,
                confidence="medium",
            )
        ]
    )

    prompt = GenerationBindingAgent().build_prompt(state)

    assert "Generation binding tasks" in prompt[1].content
    assert "phone_number" in prompt[1].content
    assert "country_code" in prompt[1].content
    assert first_task.need.target not in prompt[1].content
    assert '"step_id": "s04"' not in prompt[1].content
    assert "$.customer.phone" not in prompt[1].content
    assert "happy-path boolean confirmation or flag fields" in prompt[1].content
    assert '"values": [true]' in prompt[1].content


def test_generation_binding_decision_accepts_null_params_as_empty_dict() -> None:
    decision = GenerationBindingDecision.model_validate(
        {
            "step_id": "s05",
            "target": "$.cardToken",
            "source": "missing",
            "params": None,
            "confidence": "low",
            "reason": "Provided by external payment system.",
            "requires_human_review": True,
        }
    )

    assert decision.params == {}


def test_data_binding_assembler_places_extraction_on_source_step() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Demo", text="Demo"),
        operations=load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
        endpoint_mapping=EndpointMappingResult(
            mappings=[
                StepOperationMapping(
                    business_step="List locations",
                    operations=[OperationRef(method="GET", path="/locations")],
                ),
                StepOperationMapping(
                    business_step="Search vehicles",
                    operations=[OperationRef(method="POST", path="/vehicles/search")],
                ),
            ]
        ),
    )
    graph = build_dependency_graph(state)
    tasks = build_dependency_resolution_tasks(graph)
    task = next(item for item in tasks if item.need.target == "$.pickupLocationId")
    selected = task.candidates[0]

    plan = assemble_data_binding_plan(
        graph,
        tasks,
        [
            DependencyResolution(
                step_id=task.step_id,
                target=task.need.target,
                selected_candidate_id=selected.candidate_id,
                confidence="medium",
                reason="Location id comes from locations response.",
            )
        ],
        [],
    )

    assert plan.steps[0].response_extractions[0].json_path == selected.json_path
    assert plan.steps[1].request_bindings[0].source == "response"
    assert plan.steps[1].request_bindings[0].source_step_id == "s01"


def test_data_binding_validator_records_unknown_static_key_and_generator() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Create reservation",
                operation=OperationRef(method="POST", path="/reservations"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.countryCode",
                        location="body",
                        source="static",
                        static_key="missing_country",
                    ),
                    RequestValueBinding(
                        target="$.driverLicense",
                        location="body",
                        source="generated",
                        generator="missing_driver_license_generator",
                    ),
                ],
            )
        ]
    )

    validated = validate_data_binding(
        plan,
        load_openapi_operations("data/carsharing/openapi/openapi.yaml"),
        {"country_code": "RU"},
        GeneratorRegistry(),
    )

    assert "unknown static key" in validated.risks[0]
    assert "unknown generator" in validated.risks[1]


def test_patch_applier_replaces_request_binding() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Create reservation",
                operation=OperationRef(method="POST", path="/reservations"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.customer.driverLicenseNo",
                        location="body",
                        source="unknown",
                    )
                ],
            )
        ]
    )
    patch = BindingPatch(
        patch_type="replace_request_binding",
        step_id="s01",
        target="$.customer.driverLicenseNo",
        new_binding=RequestValueBinding(
            target="$.customer.driverLicenseNo",
            location="body",
            source="generated",
            variable="customer_driver_license_no",
            generator="uuid",
            policy="test_patch",
        ),
    )

    applied = apply_binding_patch(plan, patch, {}, GeneratorRegistry())

    assert applied is not None
    assert plan.steps[0].request_bindings[0].source == "generated"
    assert plan.steps[0].request_bindings[0].generator == "uuid"


def test_patch_applier_rejects_patch_outside_suspected_binding() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Search vehicles",
                operation=OperationRef(method="POST", path="/vehicles/search"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.driverAge",
                        location="body",
                        source="generated",
                        generator="random_int",
                        params={"min": 26, "max": 100},
                    )
                ],
            ),
            StepDataBinding(
                business_step="Create reservation",
                operation=OperationRef(method="POST", path="/reservations"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.customer.driverLicenseNo",
                        location="body",
                        source="unknown",
                    )
                ],
            ),
        ]
    )
    patch = BindingPatch(
        patch_type="replace_generated_params",
        step_id="s02",
        target="$.customer.driverLicenseNo",
        params={"min": 1000000, "max": 9999999},
    )

    applied = apply_binding_patch(
        plan,
        patch,
        {},
        GeneratorRegistry(),
        allowed_bindings=[{"step_id": "s01", "target": "$.driverAge"}],
    )

    assert applied is None
    assert plan.steps[1].request_bindings[0].source == "unknown"


def test_patch_applier_accepts_unpadded_step_id_from_fixer() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="List locations",
                operation=OperationRef(method="GET", path="/locations"),
            ),
            StepDataBinding(
                business_step="Search vehicles",
                operation=OperationRef(method="POST", path="/vehicles/search"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.returnDate",
                        location="body",
                        source="generated",
                        generator="date_after_now",
                        params={"days": 1, "format": "date"},
                    )
                ],
            ),
        ]
    )
    patch = BindingPatch(
        patch_type="replace_generated_params",
        step_id="s2",
        target="$.returnDate",
        params={"days": 2, "format": "date"},
    )

    applied = apply_binding_patch(
        plan,
        patch,
        {},
        GeneratorRegistry(),
        allowed_bindings=[{"step_id": "s02", "target": "$.returnDate"}],
    )

    assert applied is not None
    assert plan.steps[1].request_bindings[0].params == {"days": 2, "format": "date"}


def test_binding_patch_ignores_string_new_binding_for_generated_params() -> None:
    patch = BindingPatch.model_validate(
        {
            "patch_type": "replace_generated_params",
            "step_id": "s02",
            "target": "$.returnDate",
            "new_binding": "date_after_now",
            "params": {"days": 2, "format": "date"},
        }
    )

    assert patch.patch_type == "replace_generated_params"
    assert patch.new_binding is None
    assert patch.params == {"days": 2, "format": "date"}


def test_patch_applier_converts_single_value_random_int_params() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Pickup rental",
                operation=OperationRef(method="POST", path="/rentals/{reservationId}/pickup"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.fuelLevelPercent",
                        location="body",
                        source="generated",
                        generator="random_int",
                        params={"min": 0, "max": 100},
                    )
                ],
            )
        ]
    )
    patch = BindingPatch(
        patch_type="replace_generated_params",
        step_id="s01",
        target="fuelLevelPercent",
        params={"values": [100]},
    )

    applied = apply_binding_patch(
        plan,
        patch,
        {},
        GeneratorRegistry(),
        allowed_bindings=[{"step_id": "s01", "target": "$.fuelLevelPercent"}],
    )

    assert applied is not None
    assert plan.steps[0].request_bindings[0].params == {"min": 100, "max": 100}


def test_patch_applier_uses_existing_response_variable() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="List locations",
                operation=OperationRef(method="GET", path="/locations"),
                response_extractions=[
                    ResponseExtraction(variable="locations_id", json_path="$.locations[].id")
                ],
            ),
            StepDataBinding(
                business_step="Create reservation",
                operation=OperationRef(method="POST", path="/reservations"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.pickupLocationId",
                        location="body",
                        source="unknown",
                    )
                ],
            ),
        ]
    )
    patch = BindingPatch(
        patch_type="use_existing_variable",
        step_id="s02",
        target="$.pickupLocationId",
        variable="locations_id",
        reason="Use location id extracted from /locations.",
        requires_human_review=False,
    )

    applied = apply_binding_patch(
        plan,
        patch,
        {},
        GeneratorRegistry(),
        allowed_bindings=[{"step_id": "s02", "target": "$.pickupLocationId"}],
    )

    binding = plan.steps[1].request_bindings[0]
    assert applied is not None
    assert binding.source == "response"
    assert binding.variable == "locations_id"
    assert binding.location == "body"
    assert binding.target == "$.pickupLocationId"


def test_binding_patch_normalizes_string_new_binding_to_existing_variable_patch() -> None:
    patch = BindingPatch.model_validate(
        {
            "patch_type": "replace_request_binding",
            "step_id": "s04",
            "target": "$.pickupLocationId",
            "variable": "locations_id",
            "new_binding": "locations_id",
            "reason": "Use existing location variable.",
            "requires_human_review": False,
        }
    )

    assert patch.patch_type == "use_existing_variable"
    assert patch.variable == "locations_id"
    assert patch.new_binding is None


def test_stabilization_fixer_prompt_focuses_on_failed_step_only() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Demo", text=""),
        data_binding=DataBindingPlan(
            steps=[
                StepDataBinding(
                    business_step="Search vehicles",
                    operation=OperationRef(method="POST", path="/vehicles/search"),
                    request_bindings=[
                        RequestValueBinding(
                            target="$.pickupDate",
                            location="body",
                            source="generated",
                            variable="pickupDate",
                            generator="date_after_now",
                            params={"days": 1, "format": "date"},
                        ),
                        RequestValueBinding(
                            target="$.returnDate",
                            location="body",
                            source="generated",
                            variable="returnDate",
                            generator="date_after_now",
                            params={"days": 1, "format": "date"},
                        ),
                    ],
                ),
                StepDataBinding(
                    business_step="Create reservation",
                    operation=OperationRef(method="POST", path="/reservations"),
                    request_bindings=[
                        RequestValueBinding(
                            target="$.customer.driverLicenseNo",
                            location="body",
                            source="unknown",
                        )
                    ],
                ),
            ]
        ),
    )
    trace = ExecutorTrace(
        attempt=1,
        base_url="http://server",
        steps=[
            ExecutorStepTrace(
                step_id="s01",
                business_step="Search vehicles",
                operation=OperationRef(method="POST", path="/vehicles/search"),
                resolved_path="/vehicles/search",
                request={"body": {"pickupDate": "2026-06-03", "returnDate": "2026-06-03"}},
                response_status=400,
                response_body={"detail": {"code": "INVALID_DATES"}},
                status="failed",
                failure="Expected 2xx, got 400",
            )
        ],
        status="failed",
        failed_step_id="s01",
        failure="Expected 2xx, got 400",
    )
    diagnosis = StabilizationDiagnosis(
        attempt=1,
        failed_step_id="s01",
        failure_type="invalid_request_data",
        summary="returnDate must be later than pickupDate",
        suspected_bindings=[
            {
                "step_id": "s01",
                "target": "$.returnDate",
                "problem": "generated date is not after pickupDate",
            }
        ],
        recommended_fix_type="replace_generated_params",
        confidence="high",
        requires_human_review=False,
    )
    state.stabilization = StabilizationResult(
        attempts=[StabilizationAttempt(attempt=1, trace=trace, diagnosis=diagnosis)]
    )

    prompt = StabilizationFixerAgent(diagnosis=diagnosis).build_prompt(state)[1].content

    assert "$.returnDate" in prompt
    assert "$.pickupDate" in prompt
    assert "$.customer.driverLicenseNo" not in prompt
    assert '"step_id": "s04"' not in prompt


def test_executor_uses_extracted_variable_in_later_path(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/locations"):
            return FakeResponse({"locations": [{"id": "LOC-1"}]})
        return FakeResponse({"ok": True})

    monkeypatch.setattr("executor.httpx.request", fake_request)
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="List locations",
                operation=OperationRef(method="GET", path="/locations"),
                response_extractions=[
                    ResponseExtraction(variable="location_id", json_path="$.locations[].id")
                ],
            ),
            StepDataBinding(
                business_step="Open location",
                operation=OperationRef(method="GET", path="/locations/{locationId}"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.path.locationId",
                        location="path",
                        source="response",
                        variable="location_id",
                    )
                ],
            ),
        ]
    )

    trace = FlowExecutor(
        "http://server",
        {},
        generator_registry=GeneratorRegistry(),
    ).execute(plan, attempt=1)

    assert trace.status == "passed"
    assert calls[1][1] == "http://server/locations/LOC-1"


def test_executor_uses_external_context_variable(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"status": "CANCELLED"}

    calls = []

    def fake_request(method, url, params=None, headers=None, json=None, timeout=None):
        calls.append((method, url, json))
        return FakeResponse()

    monkeypatch.setattr("executor.httpx.request", fake_request)
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Cancel reservation",
                operation=OperationRef(method="POST", path="/reservations/{reservationId}/cancel"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.path.reservationId",
                        location="path",
                        source="external_context",
                        variable="reservation_id",
                    )
                ],
            )
        ]
    )

    trace = FlowExecutor(
        "http://server",
        {},
        external_context={"reservation_id": "RSV-1"},
        generator_registry=GeneratorRegistry(),
    ).execute(plan, attempt=1)

    assert trace.status == "passed"
    assert calls[0][1] == "http://server/reservations/RSV-1/cancel"


def test_executor_reuses_scenario_scoped_generated_variable(monkeypatch) -> None:
    calls = []

    def fake_request(method, url, params=None, headers=None, json=None, timeout=None):
        calls.append(json)

        class Response:
            status_code = 200

            def json(self):
                return {"ok": True}

        return Response()

    monkeypatch.setattr("executor.httpx.request", fake_request)
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Pickup",
                operation=OperationRef(method="POST", path="/pickup"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.odometer",
                        location="body",
                        source="generated",
                        variable="odometer",
                        generator="random_int",
                        params={"min": 100, "max": 100},
                        scope="scenario",
                    )
                ],
            ),
            StepDataBinding(
                business_step="Return",
                operation=OperationRef(method="POST", path="/return"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.odometer",
                        location="body",
                        source="generated",
                        variable="odometer",
                        generator="random_int",
                        params={"min": 0, "max": 0},
                        scope="scenario",
                    )
                ],
            ),
        ]
    )

    trace = FlowExecutor(
        "http://server",
        {},
        generator_registry=GeneratorRegistry(),
    ).execute(plan, attempt=1)

    assert trace.status == "passed"
    assert calls == [{"odometer": 100}, {"odometer": 100}]


def test_publish_stable_package_writes_executable_artifacts(tmp_path) -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Create reservation",
                operation=OperationRef(method="POST", path="/reservations"),
                response_extractions=[
                    ResponseExtraction(variable="reservation_id", json_path="$.id")
                ],
            )
        ]
    )
    trace = ExecutorTrace(
        attempt=1,
        base_url="http://server",
        status="passed",
        variables={"reservation_id": "RSV-1"},
        steps=[
            ExecutorStepTrace(
                step_id="s01",
                business_step="Create reservation",
                operation=OperationRef(method="POST", path="/reservations"),
                resolved_path="/reservations",
                extracted_variables={"reservation_id": "RSV-1"},
                status="passed",
            )
        ],
    )
    state = ProjectState(
        scenario=ScenarioInput(
            path="data/carsharing/specs/01-basic-economy-rental.md",
            title="Basic",
            text="",
        ),
        stabilization=StabilizationResult(
            status="passed",
            attempts=[StabilizationAttempt(attempt=1, trace=trace)],
            stable_plan=plan,
        ),
        data_binding=plan,
    )

    package_dir = publish_stable_package(
        state,
        stable_dir=tmp_path,
        openapi_path="data/carsharing/openapi/openapi.yaml",
        test_data_path=None,
        base_url="http://server",
    )

    assert package_dir is not None
    assert Path(package_dir, "stable_plan.json").exists()
    assert Path(package_dir, "last_success_trace.json").exists()
    assert Path(package_dir, "provided_state.json").exists()
    assert Path(package_dir, "metadata.json").exists()


def test_stable_package_validation_allows_test_data_additions(tmp_path) -> None:
    package_dir = tmp_path / "stable" / "scenario"
    package_dir.mkdir(parents=True)
    scenario_path = Path("data/carsharing/specs/01-basic-economy-rental.md")
    openapi_path = Path("data/carsharing/openapi/openapi.yaml")
    metadata = {
        "status": "passed",
        "scenario_hash": "will be set below",
        "openapi_hash": "will be set below",
        "test_data_hash": "old-hash",
    }
    from stable import file_sha256

    metadata["scenario_hash"] = file_sha256(scenario_path)
    metadata["openapi_hash"] = file_sha256(openapi_path)
    Path(package_dir, "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    Path(package_dir, "stable_plan.json").write_text("{}", encoding="utf-8")
    Path(package_dir, "provided_state.json").write_text("{}", encoding="utf-8")

    issues = validate_stable_package(
        package_dir,
        scenario_path,
        openapi_path,
        "data/carsharing/test-data.yaml",
    )

    assert "test data file changed after stable package was published" not in issues


def test_dependency_runner_blocks_when_dependency_is_not_stable(tmp_path, monkeypatch) -> None:
    def fake_run(self, state):
        state.understanding = ScenarioUnderstanding(
            title="Cancel reservation",
            scenario_dependencies=[
                ScenarioDependency(
                    kind="requires_scenario",
                    reference="missing-prerequisite.md",
                    reason="Cancellation requires an existing reservation.",
                )
            ],
        )
        return state, AgentRun(agent_name="Documentation Analyst", status="completed")

    monkeypatch.setattr("scenario_dependencies.DocumentationAnalystAgent.run", fake_run)

    state = ScenarioDependencyRunner().run(
        scenario_path="data/carsharing/specs/01-basic-economy-rental.md",
        openapi_path="data/carsharing/openapi/openapi.yaml",
        out_dir=tmp_path / "run",
        stable_dir=tmp_path / "stable",
        base_url="http://server",
    )

    assert state.agent_runs[-1].agent_name == "Scenario Dependency Runner"
    assert state.agent_runs[-1].status == "failed"
    assert "Cannot resolve dependency scenario" in state.agent_runs[-1].notes[0]


def test_dependency_path_resolves_scenario_number_reference() -> None:
    dependency = ScenarioDependency(
        kind="requires_scenario",
        reference="Scenario 1",
        reason="Scenario 2 depends on base rental.",
    )

    resolved = _resolve_dependency_path(dependency, Path("data/carsharing/specs"))

    assert resolved is not None
    assert Path(resolved).name == "01-basic-economy-rental.md"


def test_scenario_dependency_accepts_null_required_data() -> None:
    dependency = ScenarioDependency.model_validate(
        {
            "kind": "requires_scenario",
            "reference": "Scenario 1",
            "required_data": None,
        }
    )

    assert dependency.required_data == []


def test_context_from_setup_infers_resource_alias_from_stable_plan(tmp_path) -> None:
    package_dir = tmp_path / "stable" / "scenario"
    package_dir.mkdir(parents=True)
    output = ScenarioRunOutput(
        scenario_path="scenario.md",
        status="passed",
        provided_state=[
            ProvidedState(
                name="id",
                value="RSV-OLD",
                semantic_type="id",
                source_scenario="scenario.md",
                source_step_id="s01",
                json_path="$.id",
            )
        ],
        stable_plan=DataBindingPlan(
            steps=[
                StepDataBinding(
                    business_step="Create reservation",
                    operation=OperationRef(method="POST", path="/reservations"),
                    response_extractions=[ResponseExtraction(variable="id", json_path="$.id")],
                )
            ]
        ),
    )
    Path(package_dir, "provided_state.json").write_text(output.model_dump_json(), encoding="utf-8")

    context = _context_from_setup(package_dir, {"id": "RSV-NEW"})

    assert context["id"] == "RSV-NEW"
    assert context["reservation_id"] == "RSV-NEW"


def test_stable_setup_step_limit_uses_required_data_checkpoint(tmp_path) -> None:
    package_dir = tmp_path / "stable" / "scenario"
    package_dir.mkdir(parents=True)
    output = ScenarioRunOutput(
        scenario_path="scenario.md",
        status="passed",
        provided_state=[
            ProvidedState(
                name="id",
                value="RSV-1",
                semantic_type="id",
                source_scenario="scenario.md",
                source_step_id="s04",
                json_path="$.id",
            ),
            ProvidedState(
                name="rentalId",
                value="RNT-1",
                semantic_type="rental_id",
                source_scenario="scenario.md",
                source_step_id="s06",
                json_path="$.rentalId",
            ),
        ],
        stable_plan=DataBindingPlan(
            steps=[
                StepDataBinding(business_step="Locations", operation=OperationRef(method="GET", path="/locations")),
                StepDataBinding(business_step="Search", operation=OperationRef(method="POST", path="/vehicles/search")),
                StepDataBinding(business_step="Vehicle", operation=OperationRef(method="GET", path="/vehicles/{vehicleId}")),
                StepDataBinding(
                    business_step="Create reservation",
                    operation=OperationRef(method="POST", path="/reservations"),
                    response_extractions=[ResponseExtraction(variable="id", json_path="$.id")],
                ),
                StepDataBinding(business_step="Pay", operation=OperationRef(method="POST", path="/payments/preauth")),
                StepDataBinding(
                    business_step="Pickup",
                    operation=OperationRef(method="POST", path="/rentals/{reservationId}/pickup"),
                    response_extractions=[ResponseExtraction(variable="rentalId", json_path="$.rentalId")],
                ),
            ]
        ),
    )
    Path(package_dir, "provided_state.json").write_text(output.model_dump_json(), encoding="utf-8")
    dependency = ScenarioDependency(
        kind="requires_scenario",
        reference="Scenario 1",
        required_data=["reservation_id"],
    )

    step_limit = _stable_setup_step_limit(package_dir, dependency)

    assert step_limit == 4


def test_stable_setup_step_limit_defaults_to_first_create_id_checkpoint(tmp_path) -> None:
    package_dir = tmp_path / "stable" / "scenario"
    package_dir.mkdir(parents=True)
    output = ScenarioRunOutput(
        scenario_path="scenario.md",
        status="passed",
        provided_state=[],
        stable_plan=DataBindingPlan(
            steps=[
                StepDataBinding(business_step="Locations", operation=OperationRef(method="GET", path="/locations")),
                StepDataBinding(
                    business_step="Create reservation",
                    operation=OperationRef(method="POST", path="/reservations"),
                    response_extractions=[ResponseExtraction(variable="id", json_path="$.id")],
                ),
                StepDataBinding(business_step="Pay", operation=OperationRef(method="POST", path="/payments/preauth")),
            ]
        ),
    )
    Path(package_dir, "provided_state.json").write_text(output.model_dump_json(), encoding="utf-8")
    dependency = ScenarioDependency(kind="requires_scenario", reference="Scenario 1")

    step_limit = _stable_setup_step_limit(package_dir, dependency)

    assert step_limit == 2


def test_dependency_context_prunes_create_when_resource_id_exists() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        operations=[
            ApiOperation(
                method="POST",
                path="/reservations",
                operation_id="createReservation",
                response_schemas={"201": {"type": "object", "properties": {"id": {"type": "string"}}}},
            ),
            ApiOperation(
                method="GET",
                path="/reservations/{reservationId}",
                operation_id="getReservation",
            ),
        ],
        external_context={"reservation_id": "RSV-1"},
        endpoint_mapping=EndpointMappingResult(
            mappings=[
                StepOperationMapping(
                    business_step="Retrieve existing reservation",
                    operations=[
                        OperationRef(method="POST", path="/reservations"),
                        OperationRef(method="GET", path="/reservations/{reservationId}"),
                    ],
                )
            ]
        ),
    )

    notes = apply_dependency_context_to_endpoint_mapping(state)

    assert notes
    assert [item.path for item in state.endpoint_mapping.mappings[0].operations] == [
        "/reservations/{reservationId}"
    ]


def test_dependency_context_replaces_single_create_with_retrieve_when_resource_id_exists() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        operations=[
            ApiOperation(
                method="POST",
                path="/reservations",
                operation_id="createReservation",
                response_schemas={"201": {"type": "object", "properties": {"id": {"type": "string"}}}},
            ),
            ApiOperation(
                method="GET",
                path="/reservations/{reservationId}",
                operation_id="getReservation",
            ),
        ],
        external_context={"reservation_id": "RSV-1"},
        endpoint_mapping=EndpointMappingResult(
            mappings=[
                StepOperationMapping(
                    business_step="Create or retrieve reservation",
                    operations=[OperationRef(method="POST", path="/reservations")],
                )
            ]
        ),
    )

    notes = apply_dependency_context_to_endpoint_mapping(state)

    assert notes
    assert [item.path for item in state.endpoint_mapping.mappings[0].operations] == [
        "/reservations/{reservationId}"
    ]


def test_external_context_binding_decisions_prevent_generation_for_dependency_state() -> None:
    graph = DataDependencyGraph(
        steps=[
            DataDependencyStep(
                step_id="s01",
                business_step="Retrieve existing reservation",
                operation=OperationRef(method="GET", path="/reservations/{reservationId}"),
                needs=[
                    DataNeed(
                        step_id="s01",
                        target="$.path.reservationId",
                        location="path",
                        type="string",
                    )
                ],
            ),
        ]
    )
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        external_context={"reservation_id": "RSV-1"},
    )

    decisions = build_external_context_binding_decisions(graph, [], state)
    tasks = build_generation_binding_tasks(
        graph,
        [],
        state,
        GeneratorRegistry(),
        existing_decisions=decisions,
    )
    plan = assemble_data_binding_plan(graph, [], [], decisions)

    assert decisions[0].external_key == "reservation_id"
    assert tasks == []
    assert plan.steps[0].request_bindings[0].source == "external_context"
    assert plan.steps[0].request_bindings[0].variable == "reservation_id"


def test_static_test_data_binding_decisions_prevent_array_enum_scalar_generation() -> None:
    graph = DataDependencyGraph(
        steps=[
            DataDependencyStep(
                step_id="s01",
                business_step="Add extras",
                operation=OperationRef(method="POST", path="/reservations/{reservationId}/extras"),
                needs=[
                    DataNeed(
                        step_id="s01",
                        target="$.extras",
                        location="body",
                        type="array",
                        field_schema={"type": "array", "items": {"type": "string"}},
                    )
                ],
            ),
        ]
    )
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        static_test_data={"reservation_extras": ["CHILD_SEAT", "ADDITIONAL_DRIVER"]},
    )

    decisions = build_static_test_data_binding_decisions(graph, [], [], state)
    tasks = build_generation_binding_tasks(
        graph,
        [],
        state,
        GeneratorRegistry(),
        existing_decisions=decisions,
    )
    plan = assemble_data_binding_plan(graph, [], [], decisions)

    assert decisions[0].static_key == "reservation_extras"
    assert tasks == []
    assert plan.steps[0].request_bindings[0].source == "static"
    assert plan.steps[0].request_bindings[0].static_key == "reservation_extras"


def test_server_hint_patch_replaces_generated_random_int_params() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Pickup",
                operation=OperationRef(method="POST", path="/rentals/{reservationId}/pickup"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.fuelLevelPercent",
                        location="body",
                        source="generated",
                        variable="fuelLevelPercent",
                        generator="random_int",
                        params={"min": 0, "max": 100},
                    )
                ],
            )
        ]
    )
    trace = ExecutorTrace(
        attempt=1,
        base_url="http://server",
        status="failed",
        failed_step_id="s01",
        failure="Expected 2xx, got 400",
        steps=[
            ExecutorStepTrace(
                step_id="s01",
                business_step="Pickup",
                operation=OperationRef(method="POST", path="/rentals/{reservationId}/pickup"),
                resolved_path="/rentals/RSV-1/pickup",
                response_status=400,
                response_body={"detail": {"hint": "Use fuelLevelPercent=100"}},
                status="failed",
            )
        ],
    )
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        data_binding=plan,
        stabilization=StabilizationResult(
            attempts=[StabilizationAttempt(attempt=1, trace=trace)]
        ),
    )
    diagnosis = StabilizationDiagnosis(
        attempt=1,
        failed_step_id="s01",
        failure_type="invalid_request_data",
        summary="Fuel is not full",
        suspected_bindings=[
            {
                "step_id": "s01",
                "target": "$.fuelLevelPercent",
                "problem": "Server requires 100",
            }
        ],
    )

    patch = patch_from_server_hint(state, diagnosis)

    assert patch is not None
    assert patch.patch_type == "replace_generated_params"
    assert patch.params == {"min": 100, "max": 100}


def test_date_generator_can_return_openapi_date_format() -> None:
    value = GeneratorRegistry().generate("date_after_now", {"days": 1, "format": "date"})

    assert len(value) == 10
    assert value.count("-") == 2


def test_generator_registry_exposes_openai_tool_schema() -> None:
    schema = GeneratorRegistry().tool_schema("random_int")

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "random_int"
    assert schema["function"]["parameters"]["properties"]["min"]["type"] == "integer"
    assert schema["function"]["parameters"]["properties"]["max"]["type"] == "integer"


def test_generator_registry_includes_driver_license_generator() -> None:
    registry = GeneratorRegistry()
    value = registry.generate("driver_license_number", {"country": "RU"})
    schema = registry.tool_schema("driver_license_number")

    assert value.isdigit()
    assert len(value) == 10
    assert registry.has("driver_license_number")
    assert schema["function"]["name"] == "driver_license_number"
    assert schema["function"]["parameters"]["properties"]["country"]["type"] == "string"


def test_generator_registry_includes_payment_card_token_generator() -> None:
    registry = GeneratorRegistry()
    value = registry.generate("payment_card_token", {"provider": "mock"})
    schema = registry.tool_schema("payment_card_token")

    assert value.startswith("tok_approved_")
    assert registry.has("payment_card_token")
    assert schema["function"]["name"] == "payment_card_token"
    assert schema["function"]["parameters"]["properties"]["provider"]["type"] == "string"


def test_generator_registry_coerces_numeric_params() -> None:
    registry = GeneratorRegistry()

    assert registry.generate("random_int", {"min": "1", "max": "1"}) == 1
    date_value = registry.generate("date_after_now", {"days": "1", "format": "date"})
    assert len(date_value) == 10
    assert date_value.count("-") == 2
