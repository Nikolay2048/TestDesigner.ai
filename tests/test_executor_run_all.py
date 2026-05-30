"""
Тесты для executor_run_all (Phase 2).

HTTP-запросы мокируются. Проверяем контракт (логика pass/fail),
а не взаимодействие с реальным сервером.
"""

from unittest.mock import MagicMock, patch

from src.nodes.executor_run_all import (
    _check_assertions,
    _step_to_op_map,
    executor_run_all,
)


# ─────────────── _check_assertions ──────────────────────────────────────────
# (дублируется в test_reporter для полноты, но здесь проверяем из нужного модуля)

def test_exact_match_pass():
    assert _check_assertions([{"type": "status_code", "expected": 400}], 400)


def test_exact_match_fail():
    assert not _check_assertions([{"type": "status_code", "expected": 400}], 200)


def test_range_pass():
    assert _check_assertions([{"type": "status_code", "expected_range": [200, 299]}], 204)


def test_range_fail():
    assert not _check_assertions([{"type": "status_code", "expected_range": [200, 299]}], 404)


def test_none_status_fails():
    assert not _check_assertions([{"type": "status_code", "expected": 200}], None)


# ─────────────── _step_to_op_map ─────────────────────────────────────────────

def test_step_to_op_map_builds_correctly():
    state = {
        "all_stabilized_cards": [{
            "flow_id": "f1",
            "steps": [
                {"step_id": "step_01", "operation_id": "searchAvailableCars"},
                {"step_id": "step_02", "operation_id": "createReservationDraft"},
            ],
        }]
    }
    mapping = _step_to_op_map(state)
    assert mapping["step_01"] == "searchAvailableCars"
    assert mapping["step_02"] == "createReservationDraft"


def test_step_to_op_map_empty_state():
    assert _step_to_op_map({}) == {}


# ─────────────── executor_run_all ───────────────────────────────────────────

def _make_resp(status: int, json_data: dict = None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    resp.content = b"..."
    return resp


ENDPOINTS = [{
    "operation_id": "searchAvailableCars",
    "method": "GET",
    "path": "/api/v1/cars/availability",
    "query_params": [{"name": "cityId", "required": True}],
    "required_fields": [],
    "constraints": {},
    "response_schemas": {},
}]

ALL_STABILIZED = [{
    "flow_id": "test_flow",
    "steps": [{"step_id": "step_01", "operation_id": "searchAvailableCars"}],
}]

HAPPY_PATH_CASE = {
    "case_id": "test_flow_happy_path",
    "flow_id": "test_flow",
    "technique": "happy_path",
    "title": "Happy path",
    "target_step": "step_01",
    "setup_chain": [],
    "modified_inputs": [
        {"name": "cityId", "source": "static", "value": "77",
         "target_location": "query.cityId"},
    ],
    "expected_status": 200,
    "assertions": [{"type": "status_code", "expected_range": [200, 299]}],
    "group": "test_flow/searchAvailableCars/happy_path",
}

BOUNDARY_CASE = {
    "case_id": "test_flow_boundary_001",
    "flow_id": "test_flow",
    "technique": "boundary",
    "title": "cityId not in enum",
    "target_step": "step_01",
    "setup_chain": [],
    "modified_inputs": [
        {"name": "cityId", "source": "static", "value": "INVALID",
         "target_location": "query.cityId"},
    ],
    "expected_status": 400,
    "assertions": [{"type": "status_code", "expected": 400}],
    "group": "test_flow/searchAvailableCars/boundary",
}


@patch("src.nodes.executor_run_all.http.post")   # mock server reset
@patch("src.executor.http.request")
def test_happy_path_case_passes_on_200(mock_req, mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    mock_req.return_value = _make_resp(200, {"items": [{"carId": "car-abc"}]})

    state = {
        "test_cases": [HAPPY_PATH_CASE],
        "endpoints": ENDPOINTS,
        "all_stabilized_cards": ALL_STABILIZED,
    }
    result = executor_run_all(state)

    assert len(result["exec_results"]) == 1
    assert result["exec_results"][0]["passed"] is True
    assert result["exec_results"][0]["actual_status"] == 200


@patch("src.nodes.executor_run_all.http.post")
@patch("src.executor.http.request")
def test_happy_path_case_fails_on_400(mock_req, mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    mock_req.return_value = _make_resp(400, {"error": "bad"})

    state = {
        "test_cases": [HAPPY_PATH_CASE],
        "endpoints": ENDPOINTS,
        "all_stabilized_cards": ALL_STABILIZED,
    }
    result = executor_run_all(state)

    assert result["exec_results"][0]["passed"] is False


@patch("src.nodes.executor_run_all.http.post")
@patch("src.executor.http.request")
def test_boundary_case_passes_when_gets_400(mock_req, mock_post):
    """Граничный тест с ожидаемым 400: получили 400 → PASSED."""
    mock_post.return_value = MagicMock(status_code=200)
    mock_req.return_value = _make_resp(400, {"error": "validation"})

    state = {
        "test_cases": [BOUNDARY_CASE],
        "endpoints": ENDPOINTS,
        "all_stabilized_cards": ALL_STABILIZED,
    }
    result = executor_run_all(state)

    assert result["exec_results"][0]["passed"] is True


@patch("src.nodes.executor_run_all.http.post")
@patch("src.executor.http.request")
def test_boundary_case_fails_when_gets_200(mock_req, mock_post):
    """Граничный тест с ожидаемым 400: получили 200 → FAILED (сервер не отверг плохие данные)."""
    mock_post.return_value = MagicMock(status_code=200)
    mock_req.return_value = _make_resp(200, {"items": []})

    state = {
        "test_cases": [BOUNDARY_CASE],
        "endpoints": ENDPOINTS,
        "all_stabilized_cards": ALL_STABILIZED,
    }
    result = executor_run_all(state)

    assert result["exec_results"][0]["passed"] is False


@patch("src.nodes.executor_run_all.http.post")
@patch("src.executor.http.request")
def test_setup_chain_failure_marks_case_as_failed(mock_req, mock_post):
    """Если setup_chain упал → весь кейс FAILED."""
    mock_post.return_value = MagicMock(status_code=200)
    mock_req.return_value = _make_resp(500, {"error": "server error"})

    case_with_setup = {
        **BOUNDARY_CASE,
        "case_id": "tc_with_setup",
        "setup_chain": [{
            "step_id": "step_01",
            "operation_id": "searchAvailableCars",
            "inputs": [{"name": "cityId", "source": "static", "value": "77",
                        "target_location": "query.cityId"}],
            "produces": [],
            "depends_on": [],
        }],
    }

    state = {
        "test_cases": [case_with_setup],
        "endpoints": ENDPOINTS,
        "all_stabilized_cards": ALL_STABILIZED,
    }
    result = executor_run_all(state)

    res = result["exec_results"][0]
    assert res["passed"] is False
    assert res["failure_reason"] == "setup_chain_failed"


@patch("src.nodes.executor_run_all.http.post")
@patch("src.executor.http.request")
def test_unknown_step_id_marks_case_as_failed(mock_req, mock_post):
    """Неизвестный step_id в target_step → FAILED."""
    mock_post.return_value = MagicMock(status_code=200)

    bad_case = {**BOUNDARY_CASE, "target_step": "nonexistent_step_id"}
    state = {
        "test_cases": [bad_case],
        "endpoints": ENDPOINTS,
        "all_stabilized_cards": ALL_STABILIZED,
    }
    result = executor_run_all(state)

    res = result["exec_results"][0]
    assert res["passed"] is False
    assert "unknown_step_id" in res["failure_reason"]


@patch("src.nodes.executor_run_all.http.post")
@patch("src.executor.http.request")
def test_multiple_cases_run_independently(mock_req, mock_post):
    """Каждый кейс запускается независимо (mock_server reset между ними)."""
    mock_post.return_value = MagicMock(status_code=200)
    mock_req.side_effect = [
        _make_resp(200, {"items": []}),  # happy path → 200 → PASS
        _make_resp(400, {"error": "bad"}),  # boundary → 400 → PASS
    ]

    state = {
        "test_cases": [HAPPY_PATH_CASE, BOUNDARY_CASE],
        "endpoints": ENDPOINTS,
        "all_stabilized_cards": ALL_STABILIZED,
    }
    result = executor_run_all(state)

    assert len(result["exec_results"]) == 2
    assert all(r["passed"] for r in result["exec_results"])
    # mock server должен сброситься 2 раза
    assert mock_post.call_count == 2


def test_empty_test_cases_returns_empty():
    result = executor_run_all({"test_cases": [], "endpoints": [], "all_stabilized_cards": []})
    assert result["exec_results"] == []
