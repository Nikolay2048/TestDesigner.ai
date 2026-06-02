from __future__ import annotations

from pathlib import Path

from agents.dependency_resolver import DependencyResolverAgent
from agents.documentation_analyst import DocumentationAnalystAgent
from agents.endpoint_mapper import EndpointMapperAgent
from agents.generation_binding import GenerationBindingAgent
from data_dependencies import (
    assemble_data_binding_plan,
    build_dependency_graph,
    build_dependency_resolution_tasks,
)
from domain import (
    DataBindingPlan,
    DependencyResolverResult,
    DependencyResolution,
    EndpointMappingResult,
    OperationRef,
    ProjectState,
    RequestValueBinding,
    ScenarioInput,
    ScenarioUnderstanding,
    StepDataBinding,
    StepOperationMapping,
)
from generators import GeneratorRegistry
from io_utils import extract_raw_endpoint_mentions
from openapi import load_openapi_operations
from orchestrator import AgenticTestDesignOrchestrator
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
                        generator="driver_license_number",
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
