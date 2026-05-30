"""
Executor — фаза 1: исполнение happy-path.

Принцип 3.4: код ведёт цикл по шагам.
             Агент (stabilizer) вызывается ТОЛЬКО когда шаг упал.
Принцип 3.3: реальные HTTP-запросы через requests. Postman — финальный артефакт.

Лимит попыток: MAX_STEP_ATTEMPTS на один шаг, MAX_TOTAL_FIXES суммарно
               по всему сценарию — чтобы исключить бесконечный цикл.
"""

from typing import Any

import requests as http

from src.config import CONFIG
from src.executor import execute_step
from src.logger import get_logger
from src.models.flow import FlowCard, ScenarioStep
from src.nodes.stabilizer import stabilize_step
from src.state import GraphState

log = get_logger("executor.stabilize")

MAX_STEP_ATTEMPTS = 3    # попыток на один шаг (1 оригинал + 2 стабилизации)
MAX_TOTAL_FIXES = 9      # суммарный лимит фиксов на весь сценарий


def _reset_mock_server(base_url: str) -> None:
    """Сбрасывает состояние mock-сервера перед прогоном."""
    try:
        http.post(f"{base_url}/api/v1/mock/reset", timeout=3)
        log.info("Mock server reset OK")
    except Exception as e:
        log.debug("Mock reset skipped: %s", e)


def executor_stabilize(state: GraphState) -> dict:
    flow_card_dict = state.get("flow_card", {})
    if not flow_card_dict:
        return {"stabilized_card": {}, "trace": ["executor_stabilize"]}

    try:
        flow_card = FlowCard(**flow_card_dict)
    except Exception as e:
        return {
            "stabilized_card": {},
            "validation_errors": [f"executor: invalid flow_card: {e}"],
            "trace": ["executor_stabilize"],
        }

    endpoints: list[dict] = state.get("endpoints", [])
    ep_map = {ep["operation_id"]: ep for ep in endpoints}

    _reset_mock_server(CONFIG.base_url)

    step_context: dict[str, Any] = {}    # step_id -> response JSON
    generated_cache: dict[str, str] = {}  # field_name -> generated value
    steps_log: list[dict] = []       # все попытки всех шагов (для диагностики)
    final_results: list[dict] = []   # финальный исход каждого шага (для is_stabilized)
    total_fixes = 0

    for step in flow_card.steps:
        ep = ep_map.get(step.operation_id)
        if ep is None:
            steps_log.append({
                "step_id": step.step_id,
                "operation_id": step.operation_id,
                "passed": False,
                "error": "missing_endpoint",
                "details": f"No endpoint spec for {step.operation_id!r}",
            })
            break

        current_step: ScenarioStep = step
        step_passed = False

        for attempt in range(1, MAX_STEP_ATTEMPTS + 1):
            step_log = execute_step(current_step, ep, step_context, generated_cache, CONFIG.env_vars, CONFIG.base_url)
            step_log["stabilize_attempt"] = attempt
            steps_log.append(step_log)

            if step_log["passed"]:
                step_passed = True
                status_label = f"attempt {attempt}" if attempt > 1 else "OK"
                log.info("%s OK (%s) %s %s -> %s",
                         step.step_id, status_label,
                         step_log.get("method"), step_log.get("url"), step_log.get("status_code"))
                break

            log.warning("%s FAIL attempt %d -> %s error=%s",
                        step.step_id, attempt, step_log.get("status_code"), step_log.get("error"))
            if step_log.get("response"):
                log.debug("  Response body: %s", str(step_log.get("response"))[:300])

            if total_fixes >= MAX_TOTAL_FIXES:
                log.error("total_fixes limit (%d) reached, stopping stabilization", MAX_TOTAL_FIXES)
                break

            if attempt >= MAX_STEP_ATTEMPTS:
                break

            # Вызов агента-стабилизатора (Принцип 3.4)
            fixed_step, fix = stabilize_step(current_step, ep, step_log)

            if fixed_step is None:
                log.warning("%s stabilizer returned no fix, stopping", step.step_id)
                break

            current_step = fixed_step
            total_fixes += 1
            flow_card.stabilization_log.append({
                "step_id": step.step_id,
                "attempt": attempt,
                "reasoning": fix.reasoning,
                "fixes": [f.model_dump() for f in fix.fixes],
            })

        final_results.append(step_log)  # финальная (последняя) попытка шага

        if not step_passed:
            break  # Шаг не прошёл — дальнейшие шаги бессмысленны

    # is_stabilized = все ШАГИ прошли (финальная попытка каждого шага)
    all_passed = all(l["passed"] for l in final_results)
    if all_passed:
        flow_card.is_stabilized = True

    # Teardown: запускаем teardown_steps если основной сценарий прошёл
    # (Принцип CLAUDE.md §10: сквозные сценарии меняют состояние → teardown обязателен)
    if all_passed and flow_card.teardown_steps:
        print(f"[executor] running {len(flow_card.teardown_steps)} teardown step(s)")
        for td_step in flow_card.teardown_steps:
            ep = ep_map.get(td_step.operation_id)
            if ep is None:
                continue
            td_log = execute_step(td_step, ep, step_context, generated_cache, CONFIG.env_vars, CONFIG.base_url)
            td_log["is_teardown"] = True
            steps_log.append(td_log)
            icon = "OK" if td_log["passed"] else "WARN"
            print(f"[executor] teardown {td_step.step_id} [{icon}] -> {td_log.get('status_code')}")

    exec_result = {
        "case_id": flow_card.flow_id,
        "passed": all_passed,
        "steps_log": steps_log,
        "total_fixes_applied": total_fixes,
    }

    card_dict = flow_card.model_dump()
    return {
        "stabilized_card": card_dict,
        "all_stabilized_cards": [card_dict],  # накапливается в State через Annotated[list, add]
        "exec_results": [exec_result],
        "trace": ["executor_stabilize"],
    }
