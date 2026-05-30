"""
Тесты для executor utilities и узла executor_stabilize.

Принцип 3.1: executor — детерминированный код, тестируется без LLM.
HTTP-запросы мокируются через unittest.mock.patch.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.executor import (
    generate_value,
    resolve_jsonpath,
    resolve_binding,
    build_request,
    execute_step,
    _to_camel,
)
from src.models.flow import ScenarioStep, VariableBinding, VarSource
from src.nodes.executor_stabilize import executor_stabilize


# ─────────────── generate_value ────────────────────────────────────────────

def test_generate_uuid4_is_valid_uuid():
    import uuid
    val = generate_value("uuid4")
    uuid.UUID(val)  # raises if invalid


def test_generate_uuid4_unique():
    assert generate_value("uuid4") != generate_value("uuid4")


def test_generate_future_datetime_is_future():
    from datetime import datetime
    val = generate_value("future_datetime")
    dt = datetime.fromisoformat(val)
    assert dt > datetime.now()


def test_generate_decimal_amount():
    val = generate_value("decimal_amount")
    float(val)  # should parse as number
    assert "." in val


def test_generate_unknown_raises():
    with pytest.raises(ValueError, match="Unknown generator"):
        generate_value("nonexistent_generator")


# ─────────────── resolve_jsonpath ───────────────────────────────────────────

def test_jsonpath_root():
    data = {"a": 1}
    assert resolve_jsonpath(data, "$") == data


def test_jsonpath_simple_field():
    data = {"carId": "abc-123"}
    assert resolve_jsonpath(data, "$.carId") == "abc-123"


def test_jsonpath_nested_field():
    data = {"carLock": {"lockType": "RENTAL_RESERVATION"}}
    assert resolve_jsonpath(data, "$.carLock.lockType") == "RENTAL_RESERVATION"


def test_jsonpath_array_index():
    data = {"items": [{"carId": "car-1"}, {"carId": "car-2"}]}
    assert resolve_jsonpath(data, "$.items[0].carId") == "car-1"
    assert resolve_jsonpath(data, "$.items[1].carId") == "car-2"


def test_jsonpath_array_wildcard_uses_first():
    data = {"items": [{"carId": "car-1"}]}
    assert resolve_jsonpath(data, "$.items[*].carId") == "car-1"


def test_jsonpath_invalid_prefix_raises():
    with pytest.raises(ValueError, match="must start"):
        resolve_jsonpath({}, "carId")


# ─────────────── resolve_binding ───────────────────────────────────────────

def test_resolve_static():
    b = VariableBinding(name="cityId", source=VarSource.STATIC, value="77",
                        target_location="query.cityId")
    result = resolve_binding(b, {}, {}, {})
    assert result == "77"


def test_resolve_env_exact_key():
    b = VariableBinding(name="customerId", source=VarSource.ENV, value="customerId",
                        target_location="body.customerId")
    env = {"customerId": "cust-uuid"}
    assert resolve_binding(b, {}, {}, env) == "cust-uuid"


def test_resolve_env_uppercase_key():
    b = VariableBinding(name="paymentId", source=VarSource.ENV, value="PAYMENT_ID",
                        target_location="body.paymentId")
    env = {"paymentId": "pay-uuid", "PAYMENT_ID": "pay-uuid"}
    assert resolve_binding(b, {}, {}, env) == "pay-uuid"


def test_resolve_env_camel_fallback():
    b = VariableBinding(name="customerId", source=VarSource.ENV, value="CUSTOMER_ID",
                        target_location="body.customerId")
    # Only camelCase key available
    env = {"customerId": "cust-uuid"}
    assert resolve_binding(b, {}, {}, env) == "cust-uuid"


def test_resolve_env_missing_raises():
    b = VariableBinding(name="x", source=VarSource.ENV, value="MISSING_VAR",
                        target_location="body.x")
    with pytest.raises(KeyError, match="MISSING_VAR"):
        resolve_binding(b, {}, {}, {})


def test_resolve_generated_calls_generator():
    b = VariableBinding(name="dateFrom", source=VarSource.GENERATED,
                        generator="future_datetime", target_location="query.dateFrom")
    result = resolve_binding(b, {}, {}, {})
    from datetime import datetime
    dt = datetime.fromisoformat(result)
    assert dt > datetime.now()


def test_resolve_generated_is_cached_within_step():
    b = VariableBinding(name="dateFrom", source=VarSource.GENERATED,
                        generator="future_datetime", target_location="query.dateFrom")
    cache: dict = {}
    v1 = resolve_binding(b, {}, cache, {})
    v2 = resolve_binding(b, {}, cache, {})
    assert v1 == v2


def test_resolve_from_step():
    b = VariableBinding(name="carId", source=VarSource.FROM_STEP,
                        source_ref="step_01", source_field="$.items[0].carId",
                        target_location="body.carId")
    ctx = {"step_01": {"items": [{"carId": "abc-123"}]}}
    assert resolve_binding(b, ctx, {}, {}) == "abc-123"


def test_resolve_from_step_missing_step_raises():
    b = VariableBinding(name="carId", source=VarSource.FROM_STEP,
                        source_ref="step_01", source_field="$.carId",
                        target_location="body.carId")
    with pytest.raises(KeyError, match="step_01"):
        resolve_binding(b, {}, {}, {})


# ─────────────── _to_camel ─────────────────────────────────────────────────

def test_to_camel_single_word():
    assert _to_camel("payment") == "payment"


def test_to_camel_snake_to_camel():
    assert _to_camel("PAYMENT_ID") == "paymentId"
    assert _to_camel("CUSTOMER_ID") == "customerId"


# ─────────────── build_request ─────────────────────────────────────────────

SEARCH_STEP = ScenarioStep(
    step_id="step_01",
    operation_id="searchAvailableCars",
    inputs=[
        VariableBinding(name="cityId", source=VarSource.STATIC, value="77",
                        target_location="query.cityId"),
        VariableBinding(name="dateFrom", source=VarSource.STATIC, value="2099-01-01T10:00:00",
                        target_location="query.dateFrom"),
    ],
    produces=["$.items[0].carId"],
)

SEARCH_EP = {
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "path": "/api/v1/cars/availability",
    "path_params": [],
    "query_params": [{"name": "cityId", "required": True}],
    "required_fields": [],
    "response_schemas": {},
}

CONFIRM_STEP = ScenarioStep(
    step_id="step_03",
    operation_id="confirmReservation",
    inputs=[
        VariableBinding(name="reservationDraftId", source=VarSource.STATIC,
                        value="draft-id-123", target_location="path.reservationDraftId"),
        VariableBinding(name="paymentId", source=VarSource.STATIC,
                        value="pay-id-456", target_location="body.paymentId"),
    ],
    produces=["$.reservationId"],
)

CONFIRM_EP = {
    "operation_id": "confirmReservation",
    "method": "POST",
    "path": "/api/v1/reservations/{reservationDraftId}/confirm",
    "path_params": [{"name": "reservationDraftId"}],
    "required_fields": ["paymentId"],
    "response_schemas": {},
}


def test_build_request_get_with_query_params():
    resolved = {"cityId": "77", "dateFrom": "2099-01-01T10:00:00"}
    method, url, qp, body = build_request(SEARCH_STEP, SEARCH_EP, resolved, "http://localhost:8000")
    assert method == "GET"
    assert url == "http://localhost:8000/api/v1/cars/availability"
    assert qp["cityId"] == "77"
    assert body is None


def test_build_request_path_param_substitution():
    resolved = {"reservationDraftId": "draft-id-123", "paymentId": "pay-id-456"}
    method, url, qp, body = build_request(CONFIRM_STEP, CONFIRM_EP, resolved, "http://localhost:8000")
    assert "draft-id-123" in url
    assert "{reservationDraftId}" not in url


def test_build_request_post_body():
    resolved = {"reservationDraftId": "draft-id-123", "paymentId": "pay-id-456"}
    method, url, qp, body = build_request(CONFIRM_STEP, CONFIRM_EP, resolved, "http://localhost:8000")
    assert method == "POST"
    assert body is not None
    assert body["paymentId"] == "pay-id-456"


def test_build_request_base_url_strip_trailing_slash():
    resolved = {"cityId": "77", "dateFrom": "2099-01-01T10:00:00"}
    _, url, _, _ = build_request(SEARCH_STEP, SEARCH_EP, resolved, "http://localhost:8000/")
    assert not url.startswith("http://localhost:8000//")


# ─────────────── execute_step (HTTP мок) ───────────────────────────────────

def _make_mock_response(status: int, json_data: dict):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    resp.content = b"..."
    return resp


@patch("src.executor.http.request")
def test_execute_step_success_extracts_produces(mock_req):
    mock_req.return_value = _make_mock_response(200, {"items": [{"carId": "car-abc"}]})
    log = execute_step(SEARCH_STEP, SEARCH_EP, {}, {}, {}, "http://localhost:8000")
    assert log["passed"] is True
    assert log["status_code"] == 200
    assert "$.items[0].carId" in log["produces_extracted"]
    assert log["produces_extracted"]["$.items[0].carId"] == "car-abc"


@patch("src.executor.http.request")
def test_execute_step_failure_on_4xx(mock_req):
    mock_req.return_value = _make_mock_response(400, {"error": "invalid cityId"})
    log = execute_step(SEARCH_STEP, SEARCH_EP, {}, {}, {}, "http://localhost:8000")
    assert log["passed"] is False
    assert log["status_code"] == 400


@patch("src.executor.http.request")
def test_execute_step_writes_to_context_on_success(mock_req):
    resp_data = {"items": [{"carId": "car-abc"}]}
    mock_req.return_value = _make_mock_response(200, resp_data)
    ctx: dict = {}
    execute_step(SEARCH_STEP, SEARCH_EP, ctx, {}, {}, "http://localhost:8000")
    assert "step_01" in ctx
    assert ctx["step_01"] == resp_data


@patch("src.executor.http.request")
def test_execute_step_no_context_write_on_failure(mock_req):
    mock_req.return_value = _make_mock_response(422, {"detail": "bad"})
    ctx: dict = {}
    execute_step(SEARCH_STEP, SEARCH_EP, ctx, {}, {}, "http://localhost:8000")
    assert "step_01" not in ctx


# ─────────────── executor_stabilize node ───────────────────────────────────

SAMPLE_FLOW_CARD = {
    "flow_id": "test_flow",
    "name": "Test Flow",
    "description": "test",
    "requires_flows": [],
    "steps": [
        {
            "step_id": "step_01",
            "operation_id": "searchAvailableCars",
            "inputs": [
                {"name": "cityId", "source": "static", "value": "77",
                 "target_location": "query.cityId"},
                {"name": "dateFrom", "source": "generated", "generator": "future_datetime",
                 "target_location": "query.dateFrom"},
                {"name": "driverAge", "source": "static", "value": "30",
                 "target_location": "query.driverAge"},
            ],
            "produces": ["$.items[0].carId"],
            "depends_on": [],
        }
    ],
    "exports": [],
    "teardown_steps": [],
    "is_stabilized": False,
    "stabilization_log": [],
}

SAMPLE_ENDPOINTS = [
    {
        "operation_id": "searchAvailableCars",
        "method": "GET",
        "path": "/api/v1/cars/availability",
        "path_params": [],
        "query_params": [{"name": "cityId", "required": True}],
        "required_fields": [],
        "constraints": {},
        "request_schema": None,
        "response_schemas": {},
    }
]


@patch("src.executor.http.request")
def test_executor_node_sets_is_stabilized_on_success(mock_req):
    mock_req.return_value = _make_mock_response(200, {"items": [{"carId": "car-abc"}]})
    state = {"flow_card": SAMPLE_FLOW_CARD, "endpoints": SAMPLE_ENDPOINTS}
    result = executor_stabilize(state)
    assert result["stabilized_card"]["is_stabilized"] is True
    assert result["exec_results"][0]["passed"] is True


@patch("src.executor.http.request")
def test_executor_node_not_stabilized_on_failure(mock_req):
    mock_req.return_value = _make_mock_response(400, {"error": "bad"})
    state = {"flow_card": SAMPLE_FLOW_CARD, "endpoints": SAMPLE_ENDPOINTS}
    result = executor_stabilize(state)
    assert result["stabilized_card"]["is_stabilized"] is False
    assert result["exec_results"][0]["passed"] is False


def test_executor_node_empty_flow_card():
    result = executor_stabilize({"flow_card": {}, "endpoints": []})
    assert result["stabilized_card"] == {}
    assert "executor_stabilize" in result["trace"]


@patch("src.executor.http.request")
def test_executor_node_passes_step_context_between_steps(mock_req):
    """Шаг 2 использует carId из шага 1 через from_step."""
    step1_resp = {"items": [{"carId": "car-from-step1"}]}
    step2_resp = {"reservationDraftId": "draft-xyz", "status": "DRAFT",
                  "depositAmount": "500.00", "currency": "RUB", "expiresAt": "2099-01-02T10:00:00"}
    mock_req.side_effect = [
        _make_mock_response(200, step1_resp),
        _make_mock_response(201, step2_resp),
    ]

    two_step_flow = {
        "flow_id": "two_step",
        "name": "Two Step",
        "description": "test",
        "requires_flows": [],
        "steps": [
            {
                "step_id": "step_01",
                "operation_id": "searchAvailableCars",
                "inputs": [
                    {"name": "cityId", "source": "static", "value": "77",
                     "target_location": "query.cityId"},
                    {"name": "dateFrom", "source": "generated", "generator": "future_datetime",
                     "target_location": "query.dateFrom"},
                    {"name": "driverAge", "source": "static", "value": "30",
                     "target_location": "query.driverAge"},
                ],
                "produces": ["$.items[0].carId"],
                "depends_on": [],
            },
            {
                "step_id": "step_02",
                "operation_id": "createReservationDraft",
                "inputs": [
                    {"name": "carId", "source": "from_step", "source_ref": "step_01",
                     "source_field": "$.items[0].carId", "target_location": "body.carId"},
                    {"name": "customerId", "source": "env", "value": "customerId",
                     "target_location": "body.customerId"},
                    {"name": "cityId", "source": "static", "value": "77",
                     "target_location": "body.cityId"},
                    {"name": "dateFrom", "source": "generated", "generator": "future_datetime",
                     "target_location": "body.dateFrom"},
                    {"name": "dateTo", "source": "generated", "generator": "future_datetime",
                     "target_location": "body.dateTo"},
                    {"name": "tariffCode", "source": "static", "value": "BASE",
                     "target_location": "body.tariffCode"},
                ],
                "produces": ["$.reservationDraftId"],
                "depends_on": ["step_01"],
            },
        ],
        "exports": [],
        "teardown_steps": [],
        "is_stabilized": False,
        "stabilization_log": [],
    }

    two_endpoints = [
        *SAMPLE_ENDPOINTS,
        {
            "operation_id": "createReservationDraft",
            "method": "POST",
            "path": "/api/v1/reservations/drafts",
            "path_params": [],
            "query_params": [],
            "required_fields": ["carId", "customerId"],
            "constraints": {},
            "request_schema": None,
            "response_schemas": {},
        },
    ]

    state = {"flow_card": two_step_flow, "endpoints": two_endpoints}
    result = executor_stabilize(state)

    assert result["exec_results"][0]["passed"] is True
    # Проверим что step_02 получил carId из ответа step_01
    step2_log = result["exec_results"][0]["steps_log"][1]
    assert step2_log["request_body"]["carId"] == "car-from-step1"
