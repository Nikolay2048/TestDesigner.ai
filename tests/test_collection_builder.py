"""
Тесты для Collection Builder.

Покрываем детерминированный код — именно этот слой должен быть под тестами.
Все тесты работают с любой предметной областью.
"""
import json

import pytest

from src.collection_builder import (
    PostmanStep,
    build_collection,
    step_to_postman_item,
)
from src.config import CONFIG, RunConfig
from src.models.flow import ScenarioStep, VariableBinding, VarSource
from src.nodes.collection_builder import (
    _gen_prerequest,
    _gen_test_script,
    _jsonpath_to_js,
    _resolve_value_for_postman,
    _safe_varname,
    collection_builder,
)

POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


# ──────────────────────── _safe_varname ─────────────────────────────────────

def test_safe_varname_simple_field():
    assert _safe_varname("step_01", "$.carId") == "step_01__carId"


def test_safe_varname_array_index():
    assert _safe_varname("step_01", "$.items[0].carId") == "step_01__items__0__carId"


def test_safe_varname_root():
    assert _safe_varname("step_01", "$") == "step_01__response"


def test_safe_varname_nested():
    assert _safe_varname("step_02", "$.data.id") == "step_02__data__id"


# ──────────────────────── _jsonpath_to_js ───────────────────────────────────

def test_jsonpath_to_js_simple():
    assert _jsonpath_to_js("$.carId") == "_body.carId"


def test_jsonpath_to_js_array():
    assert _jsonpath_to_js("$.items[0].carId") == "_body.items[0].carId"


def test_jsonpath_to_js_root():
    assert _jsonpath_to_js("$") == "_body"


# ──────────────────────── _resolve_value_for_postman ────────────────────────

def _b(source, **kwargs) -> VariableBinding:
    return VariableBinding(name="field", source=source, target_location="body.field", **kwargs)


def test_resolve_static():
    b = _b(VarSource.STATIC, value="RUB")
    assert _resolve_value_for_postman(b) == "RUB"


def test_resolve_env():
    b = _b(VarSource.ENV, value="customerId")
    assert _resolve_value_for_postman(b) == "{{customerId}}"


def test_resolve_generated():
    b = VariableBinding(name="dateFrom", source=VarSource.GENERATED,
                        generator="future_datetime", target_location="query.dateFrom")
    assert _resolve_value_for_postman(b) == "{{dateFrom}}"


def test_resolve_from_step():
    b = VariableBinding(name="carId", source=VarSource.FROM_STEP,
                        source_ref="step_01", source_field="$.items[0].carId",
                        target_location="body.carId")
    assert _resolve_value_for_postman(b) == "{{step_01__items__0__carId}}"


# ──────────────────────── _gen_prerequest ───────────────────────────────────

def _gen_binding(name, gen) -> VariableBinding:
    return VariableBinding(name=name, source=VarSource.GENERATED,
                           generator=gen, target_location=f"query.{name}")


def test_prerequest_uuid4_generates_helper():
    bindings = [_gen_binding("myId", "uuid4")]
    script = "\n".join(_gen_prerequest(bindings, set()))
    assert "__uuid4" in script
    assert "pm.environment.set('myId'" in script


def test_prerequest_future_datetime():
    bindings = [_gen_binding("dateFrom", "future_datetime")]
    script = "\n".join(_gen_prerequest(bindings, set()))
    assert "setDate" in script
    assert "pm.environment.set('dateFrom'" in script


def test_prerequest_future_datetime_end():
    bindings = [_gen_binding("dateTo", "future_datetime_end")]
    script = "\n".join(_gen_prerequest(bindings, set()))
    assert "getDate() + 2" in script


def test_prerequest_decimal_amount():
    bindings = [_gen_binding("amount", "decimal_amount")]
    script = "\n".join(_gen_prerequest(bindings, set()))
    assert "1000.00" in script


def test_prerequest_skips_already_generated():
    bindings = [_gen_binding("dateFrom", "future_datetime")]
    already = {"dateFrom"}
    lines = _gen_prerequest(bindings, already)
    assert lines == []


def test_prerequest_uuid_helper_defined_once():
    bindings = [_gen_binding("id1", "uuid4"), _gen_binding("id2", "uuid4")]
    script = "\n".join(_gen_prerequest(bindings, set()))
    assert script.count("const __uuid4") == 1


# ──────────────────────── _gen_test_script ──────────────────────────────────

def _step(produces: list[str] = None) -> ScenarioStep:
    return ScenarioStep(
        step_id="step_01", operation_id="op1",
        inputs=[], produces=produces or [],
    )


def test_test_script_with_expected_status():
    lines = _gen_test_script(_step(), expected_status=201, assertions=None)
    script = "\n".join(lines)
    assert "Status 201" in script
    assert "have.status(201)" in script


def test_test_script_with_range_assertion():
    assertions = [{"type": "status_code", "expected_range": [200, 299]}]
    lines = _gen_test_script(_step(), expected_status=None, assertions=assertions)
    script = "\n".join(lines)
    assert "200" in script and "299" in script
    assert "within" in script


def test_test_script_extracts_produces():
    step = _step(produces=["$.carId", "$.status"])
    lines = _gen_test_script(step, expected_status=200, assertions=None)
    script = "\n".join(lines)
    assert "step_01__carId" in script
    assert "step_01__status" in script
    assert "_body.carId" in script
    assert "pm.environment.set" in script


def test_test_script_no_produces_no_extraction():
    lines = _gen_test_script(_step([]), expected_status=200, assertions=None)
    script = "\n".join(lines)
    assert "pm.environment.set" not in script


# ──────────────────────── Общая сборка PostmanStep / collection ─────────────

def test_step_to_postman_item_get_no_body():
    step = PostmanStep(name="Check", method="GET", path="/health")
    item = step_to_postman_item(step)
    assert item["request"]["method"] == "GET"
    assert "body" not in item["request"]


def test_step_to_postman_item_post_with_body():
    step = PostmanStep(name="Create", method="POST", path="/items",
                       body={"name": "test"})
    item = step_to_postman_item(step)
    body_parsed = json.loads(item["request"]["body"]["raw"])
    assert body_parsed["name"] == "test"


def test_step_to_postman_item_converts_path_param():
    step = PostmanStep(name="Get", method="GET", path="/items/{itemId}")
    item = step_to_postman_item(step)
    assert "{{itemId}}" in item["request"]["url"]["raw"]


def test_build_collection_name():
    col = build_collection(steps=[], name="My Suite")
    assert col["info"]["name"] == "My Suite"
    assert col["info"]["schema"] == POSTMAN_SCHEMA


def test_build_collection_variables():
    col = build_collection(steps=[], name="T",
                           variables=[{"key": "baseUrl", "value": "http://x"}])
    keys = {v["key"] for v in col["variable"]}
    assert "baseUrl" in keys


# ──────────────────────── collection_builder node ───────────────────────────

def _make_endpoint(op_id, method, path, required=None, produces_schema=None):
    """Helper to make an endpoint dict as spec_parser would produce."""
    resp_schema = {}
    if produces_schema:
        resp_schema["200"] = {
            "type": "object",
            "properties": {k: {"type": "string"} for k in produces_schema},
        }
    return {
        "operation_id": op_id,
        "method": method,
        "path": path,
        "path_params": [],
        "query_params": [],
        "request_schema": None,
        "response_schemas": resp_schema,
        "required_fields": required or [],
        "constraints": {},
    }


def _make_state_with_stabilized_card():
    """Minimal state: one stabilized flow with 2 steps."""
    step1 = ScenarioStep(
        step_id="step_01", operation_id="searchCars",
        inputs=[
            VariableBinding(name="cityId", source=VarSource.STATIC,
                            value="77", target_location="query.cityId"),
            VariableBinding(name="dateFrom", source=VarSource.GENERATED,
                            generator="future_datetime", target_location="query.dateFrom"),
        ],
        produces=["$.items[0].carId"],
    )
    step2 = ScenarioStep(
        step_id="step_02", operation_id="createDraft",
        inputs=[
            VariableBinding(name="carId", source=VarSource.FROM_STEP,
                            source_ref="step_01", source_field="$.items[0].carId",
                            target_location="body.carId"),
            VariableBinding(name="customerId", source=VarSource.ENV,
                            value="customerId", target_location="body.customerId"),
        ],
        produces=["$.draftId"],
    )
    flow = {
        "flow_id": "test_flow",
        "name": "Search and Draft",
        "description": "test",
        "steps": [step1.model_dump(), step2.model_dump()],
        "is_stabilized": True,
        "requires_flows": [],
        "exports": [],
        "teardown_steps": [],
        "stabilization_log": [],
    }
    endpoints = [
        _make_endpoint("searchCars", "GET", "/api/v1/cars", produces_schema=["items"]),
        _make_endpoint("createDraft", "POST", "/api/v1/drafts", required=["carId", "customerId"]),
    ]
    return {
        "endpoints": endpoints,
        "stabilized_card": flow,
        "all_stabilized_cards": [flow],
        "test_cases": [],
        "exec_results": [],
    }


def _make_happy_path_test_case(flow_dict: dict, step1: ScenarioStep, step2: ScenarioStep) -> dict:
    """Builds a minimal happy_path TestCase dict matching the 2-step flow."""
    from src.models.test_design import TestCase, TestTechnique
    tc = TestCase(
        case_id="test_flow_happy_path",
        flow_id=flow_dict["flow_id"],
        technique=TestTechnique.HAPPY_PATH,
        title="Happy path: Search and Draft",
        target_step=step2.step_id,
        setup_chain=[step1],
        modified_inputs=list(step2.inputs),
        expected_status=201,
        assertions=[{"type": "status_code", "expected_range": [200, 299]}],
        group=f"{flow_dict['flow_id']}/createDraft/happy_path",
    )
    return tc.model_dump(mode="json")


def _make_state_with_test_cases():
    """State with both a stabilized card AND a happy_path test case."""
    base = _make_state_with_stabilized_card()
    flow = base["stabilized_card"]
    step1 = ScenarioStep(**flow["steps"][0])
    step2 = ScenarioStep(**flow["steps"][1])
    tc = _make_happy_path_test_case(flow, step1, step2)
    return {**base, "test_cases": [tc]}


def _get_scenario_folder(col: dict) -> dict:
    """Returns the single top-level scenario folder."""
    assert len(col["item"]) == 1, f"expected 1 scenario folder, got {col['item']}"
    return col["item"][0]


def _get_technique_folder(scenario: dict, technique: str) -> dict:
    """Finds a technique subfolder by name (case-insensitive)."""
    for f in scenario["item"]:
        if technique.lower() in f["name"].lower():
            return f
    raise AssertionError(f"technique folder '{technique}' not found in {[f['name'] for f in scenario['item']]}")


# ─── node structure tests ────────────────────────────────────────────────────

def test_node_returns_collection_key():
    state = _make_state_with_stabilized_card()
    result = collection_builder(state)
    assert "collection" in result
    assert "trace" in result


def test_node_happy_path_folder_present():
    state = _make_state_with_test_cases()
    col = collection_builder(state)["collection"]
    scenario = _get_scenario_folder(col)
    tech_names = [f["name"] for f in scenario["item"]]
    assert any("Happy Path" in n for n in tech_names)


def test_node_scenario_folder_uses_flow_id_and_name():
    state = _make_state_with_test_cases()
    col = collection_builder(state)["collection"]
    scenario = _get_scenario_folder(col)
    assert "test_flow" in scenario["name"]
    assert "Search and Draft" in scenario["name"]


def test_node_has_base_url_variable():
    state = _make_state_with_stabilized_card()
    col = collection_builder(state)["collection"]
    keys = {v["key"] for v in col["variable"]}
    assert "baseUrl" in keys


def test_node_base_url_from_config():
    original = CONFIG.base_url
    CONFIG.base_url = "http://custom-host:9999"
    try:
        state = _make_state_with_stabilized_card()
        col = collection_builder(state)["collection"]
        base_var = next(v for v in col["variable"] if v["key"] == "baseUrl")
        assert base_var["value"] == "http://custom-host:9999"
    finally:
        CONFIG.base_url = original


def test_node_env_vars_in_collection_variables():
    original = CONFIG.env_vars.copy()
    CONFIG.env_vars = {"myToken": "abc123"}
    try:
        state = _make_state_with_stabilized_card()
        col = collection_builder(state)["collection"]
        keys = {v["key"] for v in col["variable"]}
        assert "myToken" in keys
    finally:
        CONFIG.env_vars = original


def test_node_step_prerequest_sets_generated_value():
    state = _make_state_with_test_cases()
    col = collection_builder(state)["collection"]
    scenario = _get_scenario_folder(col)
    hp_tech = _get_technique_folder(scenario, "happy path")
    # Each test case in the technique folder is a subfolder; first case first request
    first_case = hp_tech["item"][0]
    first_req = first_case["item"][0]  # [setup] step_01
    events = {e["listen"]: e for e in first_req.get("event", [])}
    assert "prerequest" in events
    script = "\n".join(events["prerequest"]["script"]["exec"])
    assert "dateFrom" in script


def test_node_step_test_extracts_produces():
    state = _make_state_with_test_cases()
    col = collection_builder(state)["collection"]
    scenario = _get_scenario_folder(col)
    hp_tech = _get_technique_folder(scenario, "happy path")
    first_case = hp_tech["item"][0]
    first_req = first_case["item"][0]  # [setup] step_01
    events = {e["listen"]: e for e in first_req.get("event", [])}
    assert "test" in events
    script = "\n".join(events["test"]["script"]["exec"])
    assert "step_01__items__0__carId" in script


def test_node_from_step_uses_variable_reference():
    state = _make_state_with_test_cases()
    col = collection_builder(state)["collection"]
    scenario = _get_scenario_folder(col)
    hp_tech = _get_technique_folder(scenario, "happy path")
    first_case = hp_tech["item"][0]
    # [target] step is the last request in the case
    target_req = first_case["item"][-1]
    body_raw = target_req["request"]["body"]["raw"]
    assert "{{step_01__items__0__carId}}" in body_raw


def test_node_empty_state_returns_empty_collection():
    result = collection_builder({
        "endpoints": [], "stabilized_card": {}, "test_cases": [],
        "exec_results": [], "all_stabilized_cards": [],
    })
    col = result["collection"]
    assert col["item"] == []
    assert col["info"]["schema"] == POSTMAN_SCHEMA


def test_node_collection_name_from_config():
    original = CONFIG.collection_name
    CONFIG.collection_name = "My Custom Suite"
    try:
        state = _make_state_with_test_cases()
        col = collection_builder(state)["collection"]
        assert col["info"]["name"] == "My Custom Suite"
    finally:
        CONFIG.collection_name = original
