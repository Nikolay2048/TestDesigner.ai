from __future__ import annotations

import json
from pathlib import Path

from agents.dependency_resolver import DependencyResolverAgent
from agents.documentation_analyst import DocumentationAnalystAgent
from agents.endpoint_mapper import EndpointMapperAgent
from agents.generation_binding import GenerationBindingAgent
from agents.stabilization_fixer import StabilizationFixerAgent
from agents.test_designer import TestDesignerAgent
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
    BusinessRuleAttackIdea,
    DataBindingPlan,
    DataDependencyGraph,
    DataDependencyStep,
    DataNeed,
    DataProducer,
    DependencyResolverResult,
    DependencyResolution,
    EndpointMention,
    EndpointMappingResult,
    GenerationBindingDecision,
    OperationRef,
    RawEndpointMention,
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
from postman_export import PostmanExporter, export_postman_artifacts
from scenario_dependencies import (
    ScenarioDependencyRunner,
    _add_path_placeholders_to_dependencies,
    _context_from_setup,
    _resolve_dependency_path,
    _setup_dependencies,
    _stable_setup_step_limit,
)
from stabilization_rules import patch_from_server_hint
from test_design import (
    append_business_rule_attack_ideas,
    build_case_execution_plan,
    build_test_basis,
    build_test_design,
    execute_test_cases,
)
from stable import publish_stable_package, stable_package_dir, validate_stable_package
from validators import (
    order_endpoint_mapping_by_business_steps,
    validate_data_binding,
    validate_endpoint_mapping,
)


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
    assert "activate/start an object" in prompt[1].content
    assert "not into business_steps as another command" in prompt[1].content


def test_endpoint_mention_normalizes_unknown_llm_location() -> None:
    mention = EndpointMention(
        method="POST",
        path="/mock/reset",
        location="precondition",
        note="Reset mock state.",
    )

    assert mention.location == "unknown"


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
    assert "start/activate" in prompt[1].content
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


def test_endpoint_mapping_is_reordered_to_documented_business_step_order() -> None:
    mapping = EndpointMappingResult(
        mappings=[
            StepOperationMapping(
                business_step="Pay reservation",
                operations=[OperationRef(method="POST", path="/payments")],
            ),
            StepOperationMapping(
                business_step="Create reservation",
                operations=[OperationRef(method="POST", path="/reservations")],
            ),
        ]
    )

    ordered = order_endpoint_mapping_by_business_steps(
        mapping,
        ["Create reservation", "Pay reservation"],
    )

    assert [item.business_step for item in ordered.mappings] == [
        "Create reservation",
        "Pay reservation",
    ]
    assert "reordered" in ordered.risks[0]


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


def test_data_dependency_graph_extracts_required_query_parameters() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Demo", text="Demo"),
        operations=[
            ApiOperation(
                method="GET",
                path="/services",
                operation_id="listServices",
                request_parameters=[
                    {
                        "name": "branchId",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            )
        ],
        endpoint_mapping=EndpointMappingResult(
            mappings=[
                StepOperationMapping(
                    business_step="List services",
                    operations=[OperationRef(method="GET", path="/services")],
                )
            ]
        ),
    )

    graph = build_dependency_graph(state)

    assert graph.steps[0].needs[0].target == "$.query.branchId"
    assert graph.steps[0].needs[0].location == "query"


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

    prompt = StabilizationFixerAgent(
        diagnosis=diagnosis,
        fixer_try=3,
        rejected_proposals=[
            {
                "fixer_try": 2,
                "patch": {"patch_type": "replace_generated_params"},
                "rejection_reason": "Target does not use a generator.",
            }
        ],
    ).build_prompt(state)[1].content

    assert "$.returnDate" in prompt
    assert "$.pickupDate" in prompt
    assert "$.customer.driverLicenseNo" not in prompt
    assert '"step_id": "s04"' not in prompt
    assert "Current fixer try:\n3" in prompt
    assert "Target does not use a generator." in prompt


def test_insert_operation_patch_uses_only_openapi_operation_with_complete_bindings() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Create object",
                operation=OperationRef(method="POST", path="/objects"),
                response_extractions=[
                    ResponseExtraction(variable="object_id", json_path="$.id"),
                    ResponseExtraction(variable="total_amount", json_path="$.totalAmount"),
                ],
            ),
            StepDataBinding(
                business_step="Activate object",
                operation=OperationRef(method="POST", path="/objects/{objectId}/activate"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.path.authorizationId",
                        location="path",
                        source="generated",
                        variable="authorizationId",
                        generator="uuid",
                    )
                ],
            ),
        ]
    )
    authorization_operation = ApiOperation(
        method="POST",
        path="/authorizations",
        operation_id="createAuthorization",
        request_schema={
            "type": "object",
            "required": ["objectId", "token", "amount"],
            "properties": {
                "objectId": {"type": "string"},
                "token": {"type": "string"},
                "amount": {"type": "number"},
            },
        },
    )
    patch = BindingPatch(
        patch_type="insert_operation",
        step_id="s02",
        new_step=StepDataBinding(
            business_step="Authorize required external action",
            operation=OperationRef(method="POST", path="/authorizations"),
            request_bindings=[
                RequestValueBinding(
                    target="$.objectId",
                    location="body",
                    source="response",
                    variable="object_id",
                ),
                RequestValueBinding(
                    target="$.token",
                    location="body",
                    source="generated",
                    variable="token",
                    generator="payment_card_token",
                    params={"provider": "mock"},
                ),
                RequestValueBinding(
                    target="$.amount",
                    location="body",
                    source="response",
                    variable="total_amount",
                ),
            ],
            response_extractions=[
                ResponseExtraction(
                    variable="authorizationId",
                    json_path="$.authorizationId",
                )
            ],
        ),
        reason="Server requires authorization before activation.",
        requires_human_review=True,
    )

    applied = apply_binding_patch(
        plan,
        patch,
        {},
        GeneratorRegistry(),
        allowed_bindings=[],
        allowed_operations=[authorization_operation],
    )

    assert applied is patch
    assert plan.steps[1].operation.path == "/authorizations"
    assert plan.steps[2].operation.path == "/objects/{objectId}/activate"
    rebound = plan.steps[2].request_bindings[0]
    assert rebound.source == "response"
    assert rebound.variable == "authorizationId"
    assert rebound.source_step_id == "s02"


def test_insert_operation_rejects_missing_required_bindings() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Create object",
                operation=OperationRef(method="POST", path="/objects"),
            ),
            StepDataBinding(
                business_step="Activate object",
                operation=OperationRef(method="POST", path="/objects/{objectId}/activate"),
            ),
        ]
    )
    operation = ApiOperation(
        method="POST",
        path="/authorizations",
        operation_id="createAuthorization",
        request_schema={
            "type": "object",
            "required": ["objectId"],
            "properties": {"objectId": {"type": "string"}},
        },
    )
    patch = BindingPatch(
        patch_type="insert_operation",
        step_id="s02",
        new_step=StepDataBinding(
            business_step="Authorize",
            operation=OperationRef(method="POST", path="/authorizations"),
            request_bindings=[],
        ),
    )

    try:
        apply_binding_patch(
            plan,
            patch,
            {},
            GeneratorRegistry(),
            allowed_operations=[operation],
        )
    except ValueError as exc:
        assert "missing required bindings" in str(exc)
    else:
        raise AssertionError("Incomplete inserted operation must be rejected")


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


def test_dependency_runner_treats_required_data_state_as_setup_dependency(tmp_path, monkeypatch) -> None:
    def fake_run(self, state):
        state.understanding = ScenarioUnderstanding(
            title="Complete appointment",
            scenario_dependencies=[
                ScenarioDependency(
                    kind="requires_state",
                    reference=None,
                    required_state="paid appointment exists",
                    required_data=["appointmentId"],
                    reason="Completion needs an existing appointment id.",
                )
            ],
        )
        return state, AgentRun(agent_name="Documentation Analyst", status="completed")

    monkeypatch.setattr("scenario_dependencies.DocumentationAnalystAgent.run", fake_run)

    state = ScenarioDependencyRunner().run(
        scenario_path="data/clinic/specs/04-complete-visit.md",
        openapi_path="data/clinic/openapi/openapi.yaml",
        out_dir=tmp_path / "run",
        stable_dir=tmp_path / "stable",
        base_url="http://server",
    )

    assert state.agent_runs[-1].agent_name == "Scenario Dependency Runner"
    assert state.agent_runs[-1].status == "failed"
    assert "Cannot resolve dependency scenario" in state.agent_runs[-1].notes[0]


def test_plain_required_data_is_not_treated_as_scenario_setup_dependency() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        understanding=ScenarioUnderstanding(
            title="Independent scenario",
            scenario_dependencies=[
                ScenarioDependency(
                    kind="requires_data",
                    required_data=["objectId", "userId"],
                    reason="Request needs ordinary input data.",
                )
            ],
        ),
    )

    assert _setup_dependencies(state) == []


def test_referenced_required_data_is_treated_as_scenario_setup_dependency() -> None:
    dependency = ScenarioDependency(
        kind="requires_data",
        reference="Base scenario",
        required_data=["objectId"],
        reason="The object is created by the referenced scenario.",
    )
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        understanding=ScenarioUnderstanding(
            title="Dependent scenario",
            scenario_dependencies=[dependency],
        ),
    )

    assert _setup_dependencies(state) == [dependency]


def test_dependency_path_resolves_scenario_number_reference() -> None:
    dependency = ScenarioDependency(
        kind="requires_scenario",
        reference="Scenario 1",
        reason="Scenario 2 depends on base rental.",
    )

    resolved = _resolve_dependency_path(dependency, Path("data/carsharing/specs"))

    assert resolved is not None
    assert Path(resolved).name == "01-basic-economy-rental.md"


def test_dependency_path_resolves_generic_reference_from_required_data(tmp_path) -> None:
    package_dir = tmp_path / "stable" / "01-main-happy-path"
    package_dir.mkdir(parents=True)
    output = ScenarioRunOutput(
        scenario_path="data/clinic/specs/01-main-happy-path.md",
        status="passed",
        provided_state=[
            ProvidedState(
                name="appointmentId",
                value="apt-1",
                semantic_type="appointment_id",
                source_scenario="data/clinic/specs/01-main-happy-path.md",
                source_step_id="s08",
                json_path="$.appointmentId",
            )
        ],
    )
    Path(package_dir, "provided_state.json").write_text(output.model_dump_json(), encoding="utf-8")
    dependency = ScenarioDependency(
        kind="requires_scenario",
        reference="earlier booking scenario",
        required_data=["appointmentId"],
        reason="Requires a paid appointment from an earlier booking scenario.",
    )

    resolved = _resolve_dependency_path(
        dependency,
        Path("data/clinic/specs"),
        tmp_path / "stable",
    )

    assert resolved == "data/clinic/specs/01-main-happy-path.md"


def test_dependency_path_does_not_guess_generic_reference_without_required_data(tmp_path) -> None:
    package_dir = tmp_path / "stable" / "01-main-happy-path"
    package_dir.mkdir(parents=True)
    output = ScenarioRunOutput(
        scenario_path="data/clinic/specs/01-main-happy-path.md",
        status="passed",
        provided_state=[
            ProvidedState(
                name="appointmentId",
                value="apt-1",
                semantic_type="appointment_id",
                source_scenario="data/clinic/specs/01-main-happy-path.md",
                source_step_id="s08",
                json_path="$.appointmentId",
            )
        ],
    )
    Path(package_dir, "provided_state.json").write_text(output.model_dump_json(), encoding="utf-8")
    dependency = ScenarioDependency(
        kind="requires_scenario",
        reference="earlier booking scenario",
        reason="Requires a paid appointment from an earlier booking scenario.",
    )

    resolved = _resolve_dependency_path(
        dependency,
        Path("data/clinic/specs"),
        tmp_path / "stable",
    )

    assert resolved is None


def test_scenario_dependency_accepts_null_required_data() -> None:
    dependency = ScenarioDependency.model_validate(
        {
            "kind": "requires_scenario",
            "reference": "Scenario 1",
            "required_data": None,
        }
    )

    assert dependency.required_data == []


def test_dependency_required_data_is_augmented_from_path_placeholders() -> None:
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        understanding=ScenarioUnderstanding(
            title="Extend rental",
            endpoint_mentions=[
                EndpointMention(
                    method="GET",
                    path="/rentals/{rentalId}",
                    location="step",
                )
            ],
            scenario_dependencies=[
                ScenarioDependency(
                    kind="requires_state",
                    required_state="active rental exists",
                    required_data=["reservation_id"],
                )
            ],
        ),
    )

    added = _add_path_placeholders_to_dependencies(state)

    assert added == ["rentalId"]
    assert state.understanding.scenario_dependencies[0].required_data == [
        "reservation_id",
        "rentalId",
    ]


def test_dependency_required_data_uses_raw_endpoint_mentions_when_model_omits_them() -> None:
    state = ProjectState(
        scenario=ScenarioInput(
            path="scenario.md",
            title="Scenario",
            text="",
            raw_endpoint_mentions=[
                RawEndpointMention(
                    method="GET",
                    path="/rentals/{rentalId}",
                    raw_text="GET /rentals/{rentalId}",
                    line_number=1,
                )
            ],
        ),
        understanding=ScenarioUnderstanding(
            title="Extend rental",
            endpoint_mentions=[],
            scenario_dependencies=[
                ScenarioDependency(
                    kind="requires_scenario",
                    reference="Scenario 1",
                    required_data=[],
                )
            ],
        ),
    )

    added = _add_path_placeholders_to_dependencies(state)

    assert added == ["rentalId"]
    assert state.understanding.scenario_dependencies[0].required_data == ["rentalId"]


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


def test_external_context_binding_overrides_wrong_response_candidate() -> None:
    graph = DataDependencyGraph(
        steps=[
            DataDependencyStep(
                step_id="s01",
                business_step="Search orders",
                operation=OperationRef(method="GET", path="/orders"),
                produces=[
                    DataProducer(
                        step_id="s01",
                        json_path="$.items[].id",
                        type="string",
                        field_name="id",
                        operation=OperationRef(method="GET", path="/orders"),
                    )
                ],
            ),
            DataDependencyStep(
                step_id="s02",
                business_step="Use existing order",
                operation=OperationRef(method="POST", path="/actions"),
                needs=[
                    DataNeed(
                        step_id="s02",
                        target="$.orderId",
                        location="body",
                        type="string",
                    )
                ],
            ),
        ]
    )
    tasks = build_dependency_resolution_tasks(graph)
    wrong_resolution = DependencyResolution(
        step_id="s02",
        target="$.orderId",
        selected_candidate_id=tasks[0].candidates[0].candidate_id,
        confidence="medium",
        reason="Same primitive type.",
    )
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        external_context={"order_id": "ORDER-1"},
    )

    decisions = build_external_context_binding_decisions(
        graph,
        [wrong_resolution],
        state,
    )
    plan = assemble_data_binding_plan(
        graph,
        tasks,
        [wrong_resolution],
        decisions,
    )

    binding = plan.steps[1].request_bindings[0]
    assert binding.source == "external_context"
    assert binding.variable == "order_id"


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


def test_static_test_data_binding_supports_nested_keys() -> None:
    graph = DataDependencyGraph(
        steps=[
            DataDependencyStep(
                step_id="s01",
                business_step="Authenticate",
                operation=OperationRef(method="POST", path="/auth/login"),
                needs=[
                    DataNeed(
                        step_id="s01",
                        target="$.password",
                        location="body",
                        type="string",
                        field_schema={"type": "string"},
                    )
                ],
            ),
        ]
    )
    state = ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        static_test_data={"testerAccount": {"password": "TestPassword123!"}},
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

    assert decisions[0].static_key == "testerAccount.password"
    assert tasks == []
    assert plan.steps[0].request_bindings[0].source == "static"
    assert plan.steps[0].request_bindings[0].static_key == "testerAccount.password"


def test_executor_resolves_nested_static_key(monkeypatch) -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Authenticate",
                operation=OperationRef(method="POST", path="/auth/login"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.password",
                        location="body",
                        source="static",
                        static_key="testerAccount.password",
                    )
                ],
            )
        ]
    )

    class Response:
        status_code = 200

        def json(self):
            return {"ok": True}

    captured = {}

    def fake_request(*args, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("executor.httpx.request", fake_request)

    trace = FlowExecutor(
        base_url="http://server",
        static_test_data={"testerAccount": {"password": "TestPassword123!"}},
    ).execute(plan, attempt=1)

    assert trace.status == "passed"
    assert captured["json"] == {"password": "TestPassword123!"}


def test_postman_environment_exports_nested_static_keys() -> None:
    state = _stable_generic_completion_state()
    state.static_test_data = {"testerAccount": {"password": "TestPassword123!"}}

    artifacts = PostmanExporter().export(state, base_url="http://server")
    values = {item["key"]: item["value"] for item in artifacts["environment"]["values"]}

    assert values["testerAccount_password"] == "TestPassword123!"


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
                response_body={"detail": {"hint": "Use fuelLevelPercent=100."}},
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


def test_server_hint_patch_replaces_binding_with_literal_value() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Extend rental",
                operation=OperationRef(method="POST", path="/rentals/{rentalId}/extend"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.newReturnDate",
                        location="body",
                        source="response",
                        variable="returnDate",
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
        failure="Expected 2xx, got 409",
        steps=[
            ExecutorStepTrace(
                step_id="s01",
                business_step="Extend rental",
                operation=OperationRef(method="POST", path="/rentals/{rentalId}/extend"),
                resolved_path="/rentals/RNT-1/extend",
                request={
                    "method": "POST",
                    "path": "/rentals/RNT-1/extend",
                    "query": {},
                    "headers": {},
                    "body": {"newReturnDate": "2026-06-07"},
                },
                response_status=409,
                response_body={"detail": {"hint": "Use newReturnDate=2026-06-08."}},
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
        failure_type="http_error",
        summary="Date is not after current return date",
        suspected_bindings=[
            {"step_id": "s01", "target": "$.newReturnDate", "problem": "same date"}
        ],
    )

    patch = patch_from_server_hint(state, diagnosis)

    assert patch is not None
    assert patch.patch_type == "replace_request_binding"
    assert patch.new_binding.source == "literal"
    assert patch.new_binding.literal == "2026-06-08"


def test_server_hint_patch_uses_alternate_value_from_previous_response() -> None:
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Open appointment",
                operation=OperationRef(method="GET", path="/appointments/{appointmentId}"),
                response_extractions=[
                    ResponseExtraction(variable="slotId", json_path="$.slotId")
                ],
            ),
            StepDataBinding(
                business_step="Search slots",
                operation=OperationRef(method="GET", path="/slots"),
            ),
            StepDataBinding(
                business_step="Reschedule",
                operation=OperationRef(method="PATCH", path="/appointments/{appointmentId}/reschedule"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.slotId",
                        location="body",
                        source="response",
                        variable="slotId",
                    )
                ],
            ),
        ]
    )
    trace = ExecutorTrace(
        attempt=1,
        base_url="http://server",
        status="failed",
        failed_step_id="s03",
        failure="Expected 2xx, got 409",
        steps=[
            ExecutorStepTrace(
                step_id="s01",
                business_step="Open appointment",
                operation=OperationRef(method="GET", path="/appointments/{appointmentId}"),
                resolved_path="/appointments/apt-1",
                response_status=200,
                response_body={"slotId": "slot-old"},
                request={"method": "GET", "path": "/appointments/apt-1", "query": {}, "headers": {}, "body": {}},
                status="passed",
            ),
            ExecutorStepTrace(
                step_id="s02",
                business_step="Search slots",
                operation=OperationRef(method="GET", path="/slots"),
                resolved_path="/slots",
                response_status=200,
                response_body={"items": [{"slotId": "slot-new"}]},
                request={"method": "GET", "path": "/slots", "query": {}, "headers": {}, "body": {}},
                status="passed",
            ),
            ExecutorStepTrace(
                step_id="s03",
                business_step="Reschedule",
                operation=OperationRef(method="PATCH", path="/appointments/{appointmentId}/reschedule"),
                resolved_path="/appointments/apt-1/reschedule",
                response_status=409,
                response_body={
                    "detail": {
                        "message": "New slot must differ from current slot.",
                        "hint": "Choose another slotId from GET /slots.",
                    }
                },
                request={
                    "method": "PATCH",
                    "path": "/appointments/apt-1/reschedule",
                    "query": {},
                    "headers": {},
                    "body": {"slotId": "slot-old"},
                },
                status="failed",
            ),
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
        failed_step_id="s03",
        failure_type="invalid_request_data",
        summary="Same slot selected",
        suspected_bindings=[
            {
                "step_id": "s03",
                "target": "$.slotId",
                "problem": "bound to original appointment slotId",
            }
        ],
    )

    patch = patch_from_server_hint(state, diagnosis)

    assert patch is not None
    assert patch.patch_type == "replace_request_binding"
    assert patch.new_binding.source == "response"
    assert patch.new_binding.variable == "slotId"
    assert patch.new_binding.source_step_id == "s02"
    assert patch.new_binding.json_path == "$.items[].slotId"


def test_test_basis_uses_stable_happy_path_and_openapi_schema() -> None:
    state = _stable_generic_completion_state()

    basis = build_test_basis(state)

    percent_field = next(item for item in basis.fields if item.target == "$.progressPercent")
    approval_field = next(item for item in basis.fields if item.target == "$.approved")
    assert percent_field.happy_value == 100
    assert "boundary_value_analysis" in percent_field.techniques
    assert approval_field.happy_value is True
    assert "decision_table" in approval_field.techniques


def test_test_designer_generates_reviewable_cases_from_stable_path() -> None:
    state = _stable_generic_completion_state()

    state, run = TestDesignerAgent().run(state)

    assert run.status == "completed"
    assert state.test_design is not None
    assert state.test_design.test_cases
    assert any(case.technique == "boundary_value_analysis" for case in state.test_design.test_cases)
    assert any(case.mutation.action == "omit_field" for case in state.test_design.test_cases)
    first_case = state.test_design.test_cases[0]
    assert first_case.preconditions
    assert first_case.steps
    assert first_case.expected_result
    assert first_case.tags
    assert first_case.assertions
    assert any(assertion.kind == "status_in" for assertion in first_case.assertions)
    assert state.test_design.executions[0].status == "not_run"
    assert state.test_design.executions[0].mode == "planned"


def test_test_designer_adds_setup_assertions_from_extractions_and_openapi_response() -> None:
    state = _stable_two_step_state()

    result = build_test_design(state)
    case = next(item for item in result.test_cases if item.mutated_step_id == "s02")

    setup_assertions = [item for item in case.assertions if item.step_id == "s01"]
    assert any(item.kind == "status_2xx" for item in setup_assertions)
    assert any(
        item.source == "response_extraction"
        and item.kind == "json_path_exists"
        and item.json_path == "$.id"
        for item in setup_assertions
    )
    assert any(
        item.source == "openapi_response_schema"
        and item.kind == "json_path_type"
        and item.json_path == "$.id"
        and item.expected == "string"
        for item in setup_assertions
    )


def test_test_designer_llm_refines_wording_without_changing_mutation() -> None:
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, messages):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"ideas": [], "risks": []})
            return json.dumps(
                {
                    "refinements": [
                        {
                            "idea_id": "TI-001",
                            "title": "TMS refined title",
                            "reason": "Refined rationale",
                            "expected_description": "Refined expected result",
                            "requires_human_review": True,
                        }
                    ],
                    "risks": [],
                }
            )

    state = _stable_generic_completion_state()
    state, run = TestDesignerAgent(FakeLLM()).run(state)

    assert run.status == "completed"
    assert state.test_design.ideas[0].title == "TMS refined title"
    assert state.test_design.test_cases[0].title == "TMS refined title"
    assert state.test_design.test_cases[0].mutation.target == "$.attemptCount"
    assert state.test_design.test_cases[0].expected.description == "Refined expected result"


def test_test_designer_adds_valid_llm_business_attack() -> None:
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, messages):
            self.calls += 1
            if self.calls == 1:
                return json.dumps(
                    {
                        "ideas": [
                            {
                                "rule_id": "BR-001",
                                "title": "Reject completion without approval",
                                "intent": "Verify the service enforces approval as a business condition.",
                                "mutation_type": "set_field_value",
                                "target_step_id": "s01",
                                "target": "$.approved",
                                "value": False,
                                "expected_behavior": "Completion is rejected or routed to review.",
                                "rationale": "The stable path uses approval=true; approval=false attacks the rule.",
                                "confidence": "high",
                                "requires_human_review": True,
                            }
                        ],
                        "risks": [],
                    }
                )
            return json.dumps({"refinements": [], "risks": []})

    state = _stable_generic_completion_state()
    state, run = TestDesignerAgent(FakeLLM()).run(state)

    assert run.status == "completed"
    business_cases = [
        case
        for case in state.test_design.test_cases
        if case.technique == "business_rule_violation"
        and case.title == "Reject completion without approval"
    ]
    assert business_cases
    assert business_cases[0].mutation.action == "set_value"
    assert business_cases[0].mutation.target == "$.approved"
    assert business_cases[0].mutation.value is False


def test_business_attack_replace_binding_value_uses_generated_uuid() -> None:
    state = _stable_generic_completion_state()
    result = build_test_design(state)

    notes = append_business_rule_attack_ideas(
        result,
        [
            BusinessRuleAttackIdea(
                rule_id="BR-001",
                title="Use unknown task id",
                intent="Verify unknown entity state is rejected.",
                mutation_type="replace_binding_value",
                target_step_id="s01",
                target="$.path.taskId",
                generator="uuid",
                expected_behavior="Request is rejected for unknown entity.",
                rationale="A generated UUID should not reference an existing task.",
                confidence="high",
            )
        ],
        state,
    )

    assert notes[-1] == "Business-rule executable attacks accepted: 1."
    case = next(case for case in result.test_cases if case.title == "Use unknown task id")
    plan = build_case_execution_plan(state.stabilization.stable_plan, case)
    binding = next(item for item in plan.steps[0].request_bindings if item.target == "$.path.taskId")

    assert binding.source == "generated"
    assert binding.generator == "uuid"
    assert binding.location == "path"


def test_business_attack_skip_setup_replaces_missing_producer_value_with_uuid() -> None:
    state = _stable_two_step_state()
    result = build_test_design(state)

    append_business_rule_attack_ideas(
        result,
        [
            BusinessRuleAttackIdea(
                rule_id="BR-001",
                title="Complete without created task",
                intent="Verify action is rejected when required setup entity was not created.",
                mutation_type="skip_setup_step",
                target_step_id="s02",
                skipped_step_id="s01",
                expected_behavior="Action is rejected because the entity is missing.",
                rationale="Skipping the producer step attacks business state.",
                confidence="high",
            )
        ],
        state,
    )

    case = next(case for case in result.test_cases if case.title == "Complete without created task")
    plan = build_case_execution_plan(state.stabilization.stable_plan, case)
    binding = next(item for item in plan.steps[0].request_bindings if item.target == "$.path.taskId")

    assert len(plan.steps) == 1
    assert binding.source == "generated"
    assert binding.generator == "uuid"
    assert binding.location == "path"


def test_business_attack_repeat_step_appends_duplicate_step() -> None:
    state = _stable_generic_completion_state()
    result = build_test_design(state)

    append_business_rule_attack_ideas(
        result,
        [
            BusinessRuleAttackIdea(
                rule_id="BR-001",
                title="Complete task twice",
                intent="Verify repeated state-changing action is rejected or idempotent.",
                mutation_type="repeat_step",
                target_step_id="s01",
                repeat_count=2,
                expected_behavior="Second execution is rejected or returns documented idempotent result.",
                rationale="Repeating the same action attacks state transition handling.",
                confidence="medium",
            )
        ],
        state,
    )

    case = next(case for case in result.test_cases if case.title == "Complete task twice")
    plan = build_case_execution_plan(state.stabilization.stable_plan, case)

    assert len(plan.steps) == 2
    assert plan.steps[0].operation == plan.steps[1].operation


def test_test_designer_does_not_use_conflict_status_for_validation_case() -> None:
    state = _stable_generic_completion_state()
    state.operations[0].response_statuses = ["200", "409"]

    result = build_test_design(state)

    omitted = next(case for case in result.test_cases if case.mutation.action == "omit_field")
    assert omitted.expected.status is None
    assert omitted.expected.source == "human_review"


def test_case_execution_plan_applies_literal_mutation() -> None:
    state = _stable_generic_completion_state()
    result = build_test_design(state)
    case = next(item for item in result.test_cases if item.mutation.action == "set_value")

    plan = build_case_execution_plan(state.stabilization.stable_plan, case)
    mutated_step = plan.steps[int(case.mutated_step_id.removeprefix("s")) - 1]
    binding = next(item for item in mutated_step.request_bindings if item.target == case.mutation.target)

    assert binding.source == "literal"
    assert binding.literal == case.mutation.value


def test_case_execution_plan_omits_binding() -> None:
    state = _stable_generic_completion_state()
    result = build_test_design(state)
    case = next(item for item in result.test_cases if item.mutation.action == "omit_field")

    plan = build_case_execution_plan(state.stabilization.stable_plan, case)
    mutated_step = plan.steps[int(case.mutated_step_id.removeprefix("s")) - 1]

    assert all(item.target != case.mutation.target for item in mutated_step.request_bindings)


def test_execute_test_cases_marks_expected_negative_status_as_passed(monkeypatch) -> None:
    state = _stable_generic_completion_state()
    result = build_test_design(state)
    case = next(
        item
        for item in result.test_cases
        if item.expected.status == 400 and not item.mutation.target.startswith("$.path.")
    )
    result.test_cases = [case]

    class Response:
        status_code = 400

        def json(self):
            return {"code": "VALIDATION_ERROR"}

    monkeypatch.setattr("executor.httpx.request", lambda *args, **kwargs: Response())

    records = execute_test_cases(
        result,
        state.stabilization.stable_plan,
        base_url="http://server",
        static_test_data={},
        external_context={"task_id": "TASK-1"},
    )

    assert records[0].status == "passed"
    assert records[0].actual_status == 400
    assert records[0].trace is not None


def test_execute_business_case_marks_2xx_as_weak_attack(monkeypatch) -> None:
    state = _stable_generic_completion_state()
    result = build_test_design(state)
    append_business_rule_attack_ideas(
        result,
        [
            BusinessRuleAttackIdea(
                rule_id="BR-001",
                title="Business condition still accepted",
                intent="Attack a business condition.",
                mutation_type="set_field_value",
                target_step_id="s01",
                target="$.approved",
                value=False,
                expected_behavior="Request should be rejected or require review.",
                rationale="2xx means this mutation may be too weak.",
                confidence="medium",
            )
        ],
        state,
    )
    result.test_cases = [next(case for case in result.test_cases if case.title == "Business condition still accepted")]

    class Response:
        status_code = 200

        def json(self):
            return {"status": "accepted"}

    monkeypatch.setattr("executor.httpx.request", lambda *args, **kwargs: Response())

    records = execute_test_cases(
        result,
        state.stabilization.stable_plan,
        base_url="http://server",
        static_test_data={},
        external_context={"task_id": "TASK-1"},
    )

    assert records[0].status == "weak_attack"


def test_execute_business_case_marks_4xx_without_oracle_as_oracle_incomplete(monkeypatch) -> None:
    state = _stable_generic_completion_state()
    result = build_test_design(state)
    append_business_rule_attack_ideas(
        result,
        [
            BusinessRuleAttackIdea(
                rule_id="BR-001",
                title="Business condition rejected without oracle",
                intent="Attack a business condition.",
                mutation_type="set_field_value",
                target_step_id="s01",
                target="$.approved",
                value=False,
                expected_behavior="Request should be rejected or require review.",
                rationale="4xx is useful, but exact business oracle is not formalized.",
                confidence="medium",
            )
        ],
        state,
    )
    result.test_cases = [
        next(case for case in result.test_cases if case.title == "Business condition rejected without oracle")
    ]

    class Response:
        status_code = 409

        def json(self):
            return {"code": "BUSINESS_RULE_VIOLATION"}

    monkeypatch.setattr("executor.httpx.request", lambda *args, **kwargs: Response())

    records = execute_test_cases(
        result,
        state.stabilization.stable_plan,
        base_url="http://server",
        static_test_data={},
        external_context={"task_id": "TASK-1"},
    )

    assert records[0].status == "oracle_incomplete"


def test_postman_exporter_builds_happy_path_collection() -> None:
    state = _stable_two_step_state()
    state.test_design = build_test_design(state)

    artifacts = PostmanExporter().export(state, base_url="http://server")
    collection = artifacts["happy_path_collection"]
    environment = artifacts["environment"]

    assert collection["info"]["schema"].endswith("collection/v2.1.0/collection.json")
    assert len(collection["item"]) == 2
    assert collection["item"][0]["request"]["url"]["raw"] == "{{baseUrl}}/tasks"
    second_tests = "\n".join(collection["item"][0]["event"][1]["script"]["exec"])
    assert "pm.collectionVariables.set('task_id'" in second_tests
    assert environment["values"][0]["key"] == "baseUrl"
    assert environment["values"][0]["value"] == "http://server"


def test_postman_exporter_preserves_generator_argument_order() -> None:
    state = _stable_generic_completion_state()
    state.data_binding.steps[0].request_bindings[1].generator = "date_after_now"
    state.data_binding.steps[0].request_bindings[1].params = {"format": "date", "days": 1}
    state.stabilization.stable_plan = state.data_binding

    artifacts = PostmanExporter().export(state, base_url="http://server")
    script = "\n".join(artifacts["happy_path_collection"]["item"][0]["event"][0]["script"]["exec"])

    assert "dateAfterNow(1, \"date\")" in script


def test_postman_exporter_fills_generator_defaults_before_later_args() -> None:
    state = _stable_generic_completion_state()
    state.data_binding.steps[0].request_bindings[1].generator = "date_after_now"
    state.data_binding.steps[0].request_bindings[1].params = {"format": "date"}
    state.stabilization.stable_plan = state.data_binding

    artifacts = PostmanExporter().export(state, base_url="http://server")
    script = "\n".join(artifacts["happy_path_collection"]["item"][0]["event"][0]["script"]["exec"])

    assert "dateAfterNow(1, \"date\")" in script


def test_postman_exporter_does_not_regenerate_scenario_generated_values() -> None:
    state = _stable_generic_completion_state()
    binding = state.data_binding.steps[0].request_bindings[1]
    binding.generator = "date_after_now"
    binding.params = {"format": "date", "days": 1}
    binding.scope = "scenario"
    state.stabilization.stable_plan = state.data_binding

    artifacts = PostmanExporter().export(state, base_url="http://server")
    script = "\n".join(artifacts["happy_path_collection"]["item"][0]["event"][0]["script"]["exec"])

    assert "if (pm.collectionVariables.get('attempt_count') === undefined)" in script
    assert "pm.collectionVariables.set('attempt_count', dateAfterNow(1, \"date\"));" in script


def test_postman_exporter_uses_first_scenario_generator_policy_for_same_variable() -> None:
    state = _stable_two_step_state()
    state.data_binding.steps[0].request_bindings.append(
        RequestValueBinding(
            target="$.returnDate",
            location="body",
            source="generated",
            variable="returnDate",
            generator="date_after_now",
            params={"days": 2, "format": "date"},
            scope="scenario",
        )
    )
    state.data_binding.steps[1].request_bindings.append(
        RequestValueBinding(
            target="$.returnDate",
            location="body",
            source="generated",
            variable="returnDate",
            generator="date_after_now",
            params={"days": 1, "format": "date"},
            scope="scenario",
        )
    )
    state.stabilization.stable_plan = state.data_binding

    artifacts = PostmanExporter().export(state, base_url="http://server")
    second_script = "\n".join(artifacts["happy_path_collection"]["item"][1]["event"][0]["script"]["exec"])

    assert "pm.collectionVariables.set('returnDate', dateAfterNow(2, \"date\"));" in second_script
    assert "pm.collectionVariables.set('returnDate', dateAfterNow(1, \"date\"));" not in second_script


def test_postman_exporter_builds_test_case_mutation_collection() -> None:
    state = _stable_generic_completion_state()
    state.test_design = build_test_design(state)
    case = next(item for item in state.test_design.test_cases if item.mutation.action == "set_value")
    state.test_design.test_cases = [case]

    artifacts = PostmanExporter().export(state, base_url="http://server")
    group = artifacts["test_cases_collection"]["item"][0]
    folder = group["item"][0]
    raw_body = folder["item"][-1]["request"]["body"]["raw"]
    tests = "\n".join(folder["item"][-1]["event"][1]["script"]["exec"])

    assert group["name"] == "Deterministic checks"
    assert folder["name"].startswith(case.case_id)
    assert case.technique in folder["name"]
    assert "Technique:" in folder["description"]
    assert "Mutation:" in folder["description"]
    assert str(case.mutation.value) in raw_body
    assert "Status matches expected response" in tests


def test_postman_exporter_renders_test_case_assertions() -> None:
    state = _stable_two_step_state()
    state.test_design = build_test_design(state)
    case = next(item for item in state.test_design.test_cases if item.mutated_step_id == "s02")
    state.test_design.test_cases = [case]

    artifacts = PostmanExporter().export(state, base_url="http://server")
    folder = artifacts["test_cases_collection"]["item"][0]["item"][0]
    setup_tests = "\n".join(folder["item"][0]["event"][1]["script"]["exec"])
    mutated_tests = "\n".join(folder["item"][-1]["event"][1]["script"]["exec"])

    assert "Status is 2xx" in setup_tests
    assert "$.id exists" in setup_tests
    assert "$.id has type string" in setup_tests
    assert "Status matches expected response" in mutated_tests


def test_postman_exporter_groups_llm_business_checks() -> None:
    state = _stable_generic_completion_state()
    state.test_design = build_test_design(state)
    append_business_rule_attack_ideas(
        state.test_design,
        [
            BusinessRuleAttackIdea(
                rule_id="BR-001",
                title="Business condition rejected without oracle",
                intent="Attack a business condition.",
                mutation_type="set_field_value",
                target_step_id="s01",
                target="$.approved",
                value=False,
                expected_behavior="Request should be rejected or require review.",
                rationale="4xx is useful, but exact business oracle is not formalized.",
                confidence="medium",
            )
        ],
        state,
    )

    artifacts = PostmanExporter().export(state, base_url="http://server")
    groups = artifacts["test_cases_collection"]["item"]
    business_group = next(item for item in groups if item["name"] == "LLM business checks")
    business_folder = business_group["item"][0]
    tests = "\n".join(business_folder["item"][-1]["event"][1]["script"]["exec"])

    assert "business_rule_violation" in business_folder["name"]
    assert "REVIEW:" in tests
    assert "Original:" not in tests


def test_export_postman_artifacts_writes_files(tmp_path) -> None:
    state = _stable_generic_completion_state()
    state.static_test_data = {"country_code": "RU"}
    state.test_design = build_test_design(state)

    summary = export_postman_artifacts(state, tmp_path, base_url="http://server")

    assert (tmp_path / "postman" / "happy_path.postman_collection.json").exists()
    assert (tmp_path / "postman" / "test_cases.postman_collection.json").exists()
    assert (tmp_path / "postman" / "environment.postman_environment.json").exists()
    assert summary["happy_path_requests"] == 1
    assert summary["environment_values"] == 2


def _stable_generic_completion_state() -> ProjectState:
    operation = ApiOperation(
        method="POST",
        path="/tasks/{taskId}/complete",
        operation_id="completeTask",
        request_schema={
            "type": "object",
            "required": ["attemptCount", "progressPercent", "approved"],
            "properties": {
                "attemptCount": {"type": "integer", "minimum": 0},
                "progressPercent": {"type": "integer", "minimum": 0, "maximum": 100},
                "approved": {"type": "boolean"},
            },
        },
        response_statuses=["200", "400"],
    )
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Complete task",
                operation=OperationRef(method="POST", path="/tasks/{taskId}/complete"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.path.taskId",
                        location="path",
                        source="external_context",
                        variable="task_id",
                    ),
                    RequestValueBinding(
                        target="$.attemptCount",
                        location="body",
                        source="generated",
                        variable="attempt_count",
                        generator="random_int",
                        params={"min": 1, "max": 1},
                    ),
                    RequestValueBinding(
                        target="$.progressPercent",
                        location="body",
                        source="generated",
                        variable="progress_percent",
                        generator="random_int",
                        params={"min": 100, "max": 100},
                    ),
                    RequestValueBinding(
                        target="$.approved",
                        location="body",
                        source="generated",
                        variable="approved",
                        generator="enum_value",
                        params={"values": [True]},
                    ),
                ],
            )
        ]
    )
    trace = ExecutorTrace(
        attempt=1,
        base_url="http://server",
        status="passed",
        steps=[
            ExecutorStepTrace(
                step_id="s01",
                business_step="Complete task",
                operation=OperationRef(method="POST", path="/tasks/{taskId}/complete"),
                resolved_path="/tasks/TASK-1/complete",
                request={
                    "method": "POST",
                    "path": "/tasks/TASK-1/complete",
                    "body": {
                        "attemptCount": 1,
                        "progressPercent": 100,
                        "approved": True,
                    },
                },
                response_status=200,
                response_body={"resultId": "RESULT-1"},
                status="passed",
            )
        ],
    )
    return ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Scenario", text=""),
        operations=[operation],
        data_binding=plan,
        understanding=ScenarioUnderstanding(
            title="Scenario",
            business_rules=["Completion requires full progress and explicit approval."],
        ),
        stabilization=StabilizationResult(
            status="passed",
            attempts=[StabilizationAttempt(attempt=1, trace=trace)],
            stable_plan=plan,
        ),
    )


def _stable_two_step_state() -> ProjectState:
    create_operation = ApiOperation(
        method="POST",
        path="/tasks",
        operation_id="createTask",
        request_schema={
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        },
        response_schemas={
            "201": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            }
        },
        response_statuses=["201", "400"],
    )
    complete_operation = ApiOperation(
        method="POST",
        path="/tasks/{taskId}/complete",
        operation_id="completeTask",
        request_schema={
            "type": "object",
            "required": ["approved"],
            "properties": {"approved": {"type": "boolean"}},
        },
        response_statuses=["200", "400", "404", "409"],
    )
    plan = DataBindingPlan(
        steps=[
            StepDataBinding(
                business_step="Create task",
                operation=OperationRef(method="POST", path="/tasks"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.name",
                        location="body",
                        source="literal",
                        literal="test task",
                    )
                ],
                response_extractions=[
                    ResponseExtraction(variable="task_id", json_path="$.id", source_step_id="s01")
                ],
            ),
            StepDataBinding(
                business_step="Complete created task",
                operation=OperationRef(method="POST", path="/tasks/{taskId}/complete"),
                request_bindings=[
                    RequestValueBinding(
                        target="$.path.taskId",
                        location="path",
                        source="response",
                        variable="task_id",
                        source_step_id="s01",
                    ),
                    RequestValueBinding(
                        target="$.approved",
                        location="body",
                        source="literal",
                        literal=True,
                    ),
                ],
            ),
        ]
    )
    trace = ExecutorTrace(
        attempt=1,
        base_url="http://server",
        status="passed",
        steps=[
            ExecutorStepTrace(
                step_id="s01",
                business_step="Create task",
                operation=OperationRef(method="POST", path="/tasks"),
                resolved_path="/tasks",
                request={"method": "POST", "path": "/tasks", "body": {"name": "test task"}},
                response_status=201,
                response_body={"id": "TASK-1"},
                extracted_variables={"task_id": "TASK-1"},
                status="passed",
            ),
            ExecutorStepTrace(
                step_id="s02",
                business_step="Complete created task",
                operation=OperationRef(method="POST", path="/tasks/{taskId}/complete"),
                resolved_path="/tasks/TASK-1/complete",
                request={
                    "method": "POST",
                    "path": "/tasks/TASK-1/complete",
                    "body": {"approved": True},
                },
                response_status=200,
                response_body={"status": "completed"},
                status="passed",
            ),
        ],
    )
    return ProjectState(
        scenario=ScenarioInput(path="scenario.md", title="Two-step scenario", text=""),
        operations=[create_operation, complete_operation],
        data_binding=plan,
        understanding=ScenarioUnderstanding(
            title="Two-step scenario",
            business_rules=["A task can be completed only after it exists."],
        ),
        stabilization=StabilizationResult(
            status="passed",
            attempts=[StabilizationAttempt(attempt=1, trace=trace)],
            stable_plan=plan,
        ),
    )


def test_date_generator_can_return_openapi_date_format() -> None:
    value = GeneratorRegistry().generate("date_after_now", {"days": 1, "format": "date"})

    assert len(value) == 10
    assert value.count("-") == 2


def test_generator_registry_uses_valid_default_email_domain() -> None:
    value = GeneratorRegistry().generate("email")

    assert value.endswith("@test.com")


def test_generator_registry_generates_ru_e164_phone_with_ten_digits_after_country_code() -> None:
    value = GeneratorRegistry().generate("phone_number", {"country": "RU", "format": "e164"})

    assert value.startswith("+7")
    assert len(value) == 12
    assert value[2:].isdigit()


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
