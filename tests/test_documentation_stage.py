from __future__ import annotations

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
)
from domain import (
    BindingPatch,
    DataBindingPlan,
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
    StabilizationAttempt,
    StabilizationDiagnosis,
    StabilizationResult,
    ScenarioUnderstanding,
    StepDataBinding,
    StepOperationMapping,
)
from executor import FlowExecutor
from generators import GeneratorRegistry
from io_utils import extract_raw_endpoint_mentions
from openapi import load_openapi_operations
from orchestrator import AgenticTestDesignOrchestrator
from patches import apply_binding_patch
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

    trace = FlowExecutor("http://server", {}, GeneratorRegistry()).execute(plan, attempt=1)

    assert trace.status == "passed"
    assert calls[1][1] == "http://server/locations/LOC-1"


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
