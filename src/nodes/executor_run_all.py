"""
Executor — фаза 2: прогон всех тест-кейсов.

Для каждого TestCase:
  1. Сбросить mock-сервер (изоляция между кейсами).
  2. Прогнать setup_chain (собирает контекст: carId, draftId, ...).
  3. Прогнать target-шаг с modified_inputs.
  4. Проверить assertions (expected_status, expected_range).

"Passed" для негативных кейсов = получили ожидаемый 4xx.
Стабилизатор в Phase 2 НЕ вызывается — падение фиксируется как факт.
"""

import requests as http

from src.config import CONFIG
from src.executor import execute_step
from src.logger import get_logger
from src.models.flow import ScenarioStep, VariableBinding, VarSource
from src.models.test_design import TestCase
from src.state import GraphState

log = get_logger("executor.run_all")


def _reset_mock_server(base_url: str) -> None:
    try:
        http.post(f"{base_url}/api/v1/mock/reset", timeout=3)
    except Exception:
        pass


def _check_assertions(assertions: list[dict], actual_status: int | None) -> bool:
    """Возвращает True, если все assertions выполнены."""
    if actual_status is None:
        return False
    for a in assertions:
        if a.get("type") == "status_code":
            if "expected" in a and actual_status != a["expected"]:
                return False
            if "expected_range" in a:
                lo, hi = a["expected_range"]
                if not (lo <= actual_status <= hi):
                    return False
    return True


def _step_to_op_map(state: GraphState) -> dict[str, str]:
    """Строит маппинг step_id → operation_id из всех стабилизированных карточек."""
    mapping: dict[str, str] = {}
    for card in state.get("all_stabilized_cards", []):
        for step in card.get("steps", []):
            mapping[step["step_id"]] = step["operation_id"]
    return mapping


def executor_run_all(state: GraphState) -> dict:
    test_cases_raw: list[dict] = state.get("test_cases", [])
    if not test_cases_raw:
        return {"exec_results": [], "trace": ["executor_run_all"]}

    endpoints: list[dict] = state.get("endpoints", [])
    ep_map = {ep["operation_id"]: ep for ep in endpoints}
    step_to_op = _step_to_op_map(state)

    results: list[dict] = []
    passed_count = 0

    for tc_dict in test_cases_raw:
        try:
            tc = TestCase(**tc_dict)
        except Exception as e:
            results.append({
                "case_id": tc_dict.get("case_id", "?"),
                "passed": False,
                "failure_reason": f"invalid_test_case: {e}",
                "steps_log": [],
            })
            continue

        _reset_mock_server(CONFIG.base_url)
        step_context: dict = {}
        generated_cache: dict = {}
        steps_log: list[dict] = []
        setup_failed = False

        # 1. Прогон setup_chain
        for setup_step in tc.setup_chain:
            ep = ep_map.get(setup_step.operation_id)
            if ep is None:
                steps_log.append({
                    "step_id": setup_step.step_id,
                    "passed": False,
                    "error": "missing_endpoint",
                    "details": f"No spec for {setup_step.operation_id!r}",
                })
                setup_failed = True
                break
            step_result = execute_step(setup_step, ep, step_context, generated_cache, CONFIG.env_vars, CONFIG.base_url)
            steps_log.append(step_result)
            if not step_result["passed"]:
                setup_failed = True
                break

        if setup_failed:
            results.append({
                "case_id": tc.case_id,
                "title": tc.title,
                "technique": tc.technique.value,
                "passed": False,
                "actual_status": None,
                "expected_status": tc.expected_status,
                "failure_reason": "setup_chain_failed",
                "steps_log": steps_log,
            })
            continue

        # 2. Прогон target-шага с modified_inputs
        op_id = step_to_op.get(tc.target_step, "")
        if not op_id:
            results.append({
                "case_id": tc.case_id,
                "title": tc.title,
                "technique": tc.technique.value,
                "passed": False,
                "failure_reason": f"unknown_step_id: {tc.target_step!r}",
                "steps_log": steps_log,
            })
            continue

        ep = ep_map.get(op_id)
        if ep is None:
            results.append({
                "case_id": tc.case_id,
                "title": tc.title,
                "technique": tc.technique.value,
                "passed": False,
                "failure_reason": f"missing_endpoint: {op_id!r}",
                "steps_log": steps_log,
            })
            continue

        target_step = ScenarioStep(
            step_id=tc.target_step,
            operation_id=op_id,
            inputs=tc.modified_inputs,
            produces=[],
        )
        step_result = execute_step(target_step, ep, step_context, generated_cache, CONFIG.env_vars, CONFIG.base_url)
        steps_log.append(step_result)

        # 3. Проверка assertions
        actual_status = step_result.get("status_code")
        passed = _check_assertions(tc.assertions, actual_status)
        if passed:
            passed_count += 1

        results.append({
            "case_id": tc.case_id,
            "title": tc.title,
            "technique": tc.technique.value,
            "passed": passed,
            "actual_status": actual_status,
            "expected_status": tc.expected_status,
            "steps_log": steps_log,
        })

    total = len(results)
    failed_count = total - passed_count
    log.info("Phase 2 complete: %d/%d passed, %d failed", passed_count, total, failed_count)
    for r in results:
        if not r.get("passed"):
            log.warning("  FAIL [%s] %s | actual=%s exp=%s",
                        r.get("technique", "?"), r.get("title", "?"),
                        r.get("actual_status"), r.get("expected_status"))
        else:
            log.debug("  PASS [%s] %s", r.get("technique", "?"), r.get("title", "?"))
    return {"exec_results": results, "trace": ["executor_run_all"]}
