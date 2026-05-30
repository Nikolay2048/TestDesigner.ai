"""
Тесты для Reporter (Этап 9).

Reporter — детерминированный код: тестируется напрямую без моков.
"""

from src.nodes.reporter import (
    _diagnosis_summary,
    _endpoint_coverage,
    _execution_summary,
    _op_id_from_group,
    _stabilization_summary,
    _test_case_breakdown,
    reporter,
)
from src.nodes.executor_run_all import _check_assertions


# ─────────────── _op_id_from_group ──────────────────────────────────────────

def test_op_id_from_group_standard_format():
    assert _op_id_from_group("test_flow/searchAvailableCars/boundary") == "searchAvailableCars"


def test_op_id_from_group_happy_path_format():
    assert _op_id_from_group("test_flow/createReservationDraft/happy_path") == "createReservationDraft"


def test_op_id_from_group_empty_string():
    assert _op_id_from_group("") == ""


def test_op_id_from_group_only_flow():
    assert _op_id_from_group("flow_only") == ""


# ─────────────── _check_assertions ──────────────────────────────────────────

def test_check_assertions_exact_match_pass():
    assert _check_assertions([{"type": "status_code", "expected": 400}], 400) is True


def test_check_assertions_exact_match_fail():
    assert _check_assertions([{"type": "status_code", "expected": 400}], 200) is False


def test_check_assertions_range_pass():
    assert _check_assertions([{"type": "status_code", "expected_range": [200, 299]}], 201) is True


def test_check_assertions_range_fail():
    assert _check_assertions([{"type": "status_code", "expected_range": [200, 299]}], 400) is False


def test_check_assertions_none_status_fails():
    assert _check_assertions([{"type": "status_code", "expected": 200}], None) is False


def test_check_assertions_empty_list_passes():
    assert _check_assertions([], 200) is True


# ─────────────── _endpoint_coverage ─────────────────────────────────────────

ENDPOINTS = [
    {"operation_id": "searchAvailableCars"},
    {"operation_id": "createReservationDraft"},
    {"operation_id": "confirmReservation"},
]

TEST_CASES_ALL_COVERED = [
    {"group": "flow/searchAvailableCars/boundary"},
    {"group": "flow/createReservationDraft/negative"},
    {"group": "flow/confirmReservation/happy_path"},
]

TEST_CASES_PARTIAL = [
    {"group": "flow/searchAvailableCars/boundary"},
    {"group": "flow/createReservationDraft/negative"},
]


def test_coverage_all_covered():
    cov = _endpoint_coverage(ENDPOINTS, TEST_CASES_ALL_COVERED)
    assert cov["total"] == 3
    assert cov["covered"] == 3
    assert cov["uncovered"] == []
    assert cov["coverage_pct"] == 100.0


def test_coverage_partial():
    cov = _endpoint_coverage(ENDPOINTS, TEST_CASES_PARTIAL)
    assert cov["covered"] == 2
    assert "confirmReservation" in cov["uncovered"]


def test_coverage_no_test_cases():
    cov = _endpoint_coverage(ENDPOINTS, [])
    assert cov["covered"] == 0
    assert cov["coverage_pct"] == 0.0
    assert len(cov["uncovered"]) == 3


def test_coverage_no_endpoints():
    cov = _endpoint_coverage([], TEST_CASES_ALL_COVERED)
    assert cov["total"] == 0
    assert cov["coverage_pct"] == 0.0


def test_coverage_pct_calculation():
    cov = _endpoint_coverage(ENDPOINTS, TEST_CASES_PARTIAL)
    assert cov["coverage_pct"] == round(100.0 * 2 / 3, 1)


# ─────────────── _test_case_breakdown ────────────────────────────────────────

TEST_CASES_MIXED = [
    {"technique": "happy_path", "group": "f/searchAvailableCars/happy_path"},
    {"technique": "boundary", "group": "f/searchAvailableCars/boundary"},
    {"technique": "boundary", "group": "f/searchAvailableCars/boundary"},
    {"technique": "negative", "group": "f/createReservationDraft/negative"},
]


def test_breakdown_total():
    b = _test_case_breakdown(TEST_CASES_MIXED)
    assert b["total"] == 4


def test_breakdown_by_technique():
    b = _test_case_breakdown(TEST_CASES_MIXED)
    assert b["by_technique"]["happy_path"] == 1
    assert b["by_technique"]["boundary"] == 2
    assert b["by_technique"]["negative"] == 1


def test_breakdown_by_endpoint():
    b = _test_case_breakdown(TEST_CASES_MIXED)
    assert b["by_endpoint"]["searchAvailableCars"] == 3
    assert b["by_endpoint"]["createReservationDraft"] == 1


def test_breakdown_empty():
    b = _test_case_breakdown([])
    assert b["total"] == 0
    assert b["by_technique"] == {}


# ─────────────── _execution_summary ─────────────────────────────────────────

EXEC_RESULTS_PHASE2 = [
    {"case_id": "c1", "title": "Happy path", "technique": "happy_path", "passed": True},
    {"case_id": "c2", "title": "Missing field", "technique": "negative", "passed": True},
    {"case_id": "c3", "title": "Invalid enum", "technique": "boundary", "passed": False,
     "failure_reason": "unexpected_status"},
]

EXEC_RESULTS_PHASE1_ONLY = [
    # Phase 1 results do NOT have 'title'
    {"case_id": "flow1", "passed": True, "steps_log": [], "total_fixes_applied": 1},
]


def test_execution_summary_no_phase2():
    s = _execution_summary(EXEC_RESULTS_PHASE1_ONLY)
    assert s["total_run"] == 0
    assert s["pass_rate_pct"] is None


def test_execution_summary_with_phase2():
    s = _execution_summary(EXEC_RESULTS_PHASE2)
    assert s["total_run"] == 3
    assert s["passed"] == 2
    assert s["failed"] == 1


def test_execution_summary_pass_rate():
    s = _execution_summary(EXEC_RESULTS_PHASE2)
    assert s["pass_rate_pct"] == round(100.0 * 2 / 3, 1)


def test_execution_summary_failed_cases_list():
    s = _execution_summary(EXEC_RESULTS_PHASE2)
    assert len(s["failed_cases"]) == 1
    assert s["failed_cases"][0]["case_id"] == "c3"


def test_execution_summary_empty():
    s = _execution_summary([])
    assert s["total_run"] == 0


# ─────────────── _diagnosis_summary ─────────────────────────────────────────

DIAGNOSES = [
    {"category": "test_data_issue", "needs_human": False},
    {"category": "test_data_issue", "needs_human": False},
    {"category": "service_bug", "needs_human": True},
]


def test_diagnosis_summary_total():
    d = _diagnosis_summary(DIAGNOSES)
    assert d["total"] == 3


def test_diagnosis_summary_by_category():
    d = _diagnosis_summary(DIAGNOSES)
    assert d["by_category"]["test_data_issue"] == 2
    assert d["by_category"]["service_bug"] == 1


def test_diagnosis_summary_needs_human():
    d = _diagnosis_summary(DIAGNOSES)
    assert d["needs_human"] == 1


def test_diagnosis_summary_empty():
    d = _diagnosis_summary([])
    assert d["total"] == 0
    assert d["needs_human"] == 0


# ─────────────── _stabilization_summary ──────────────────────────────────────

def test_stabilization_summary_is_stabilized():
    stab = _stabilization_summary({"is_stabilized": True, "flow_id": "f1", "name": "Flow 1"}, [])
    assert stab["is_stabilized"] is True
    assert stab["flow_id"] == "f1"


def test_stabilization_summary_total_fixes():
    phase1 = [{"total_fixes_applied": 2}, {"total_fixes_applied": 1}]
    stab = _stabilization_summary({"is_stabilized": True, "flow_id": "f", "name": "f"}, phase1)
    assert stab["total_fixes_applied"] == 3


def test_stabilization_summary_ignores_phase2():
    # Phase 2 results have 'title', should not count their fixes
    phase1 = [{"total_fixes_applied": 1}]
    phase2 = [{"title": "Happy path", "total_fixes_applied": 99}]
    stab = _stabilization_summary({"is_stabilized": True, "flow_id": "f", "name": "f"}, phase1 + phase2)
    assert stab["total_fixes_applied"] == 1


# ─────────────── reporter node ───────────────────────────────────────────────

FULL_STATE = {
    "endpoints": ENDPOINTS,
    "test_cases": TEST_CASES_MIXED,
    "exec_results": EXEC_RESULTS_PHASE2 + EXEC_RESULTS_PHASE1_ONLY,
    "stabilized_card": {"is_stabilized": True, "flow_id": "test_flow", "name": "Test"},
    "diagnoses": DIAGNOSES,
}


def test_reporter_returns_metrics():
    result = reporter(FULL_STATE)
    assert "metrics" in result
    assert "trace" in result


def test_reporter_metrics_structure():
    result = reporter(FULL_STATE)
    m = result["metrics"]
    assert "endpoints" in m
    assert "test_cases" in m
    assert "execution" in m
    assert "diagnosis" in m
    assert "stabilization" in m


def test_reporter_metrics_endpoint_total():
    result = reporter(FULL_STATE)
    assert result["metrics"]["endpoints"]["total"] == 3


def test_reporter_metrics_test_cases_total():
    result = reporter(FULL_STATE)
    assert result["metrics"]["test_cases"]["total"] == 4


def test_reporter_metrics_execution_passed():
    result = reporter(FULL_STATE)
    assert result["metrics"]["execution"]["passed"] == 2


def test_reporter_empty_state():
    result = reporter({})
    m = result["metrics"]
    assert m["endpoints"]["total"] == 0
    assert m["test_cases"]["total"] == 0
    assert m["execution"]["total_run"] == 0
