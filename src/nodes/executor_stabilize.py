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
from src.models.flow import FlowCard, ScenarioStep
from src.nodes.stabilizer import stabilize_step
from src.state import GraphState

MAX_STEP_ATTEMPTS = 3    # попыток на один шаг (1 оригинал + 2 стабилизации)
MAX_TOTAL_FIXES = 9      # суммарный лимит фиксов на весь сценарий


def _reset_mock_server(base_url: str) -> None:
    """Сбрасывает состояние mock-сервера перед прогоном."""
    try:
        http.post(f"{base_url}/api/v1/mock/reset", timeout=3)
        print("[executor] mock server reset OK")
    except Exception:
        pass  # сервер недоступен или не поддерживает reset


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
            log = execute_step(current_step, ep, step_context, generated_cache, CONFIG.env_vars, CONFIG.base_url)
            log["stabilize_attempt"] = attempt
            steps_log.append(log)

            if log["passed"]:
                step_passed = True
                status = f"attempt {attempt}" if attempt > 1 else "OK"
                print(
                    f"[executor] {step.step_id} OK ({status}) "
                    f"{log.get('method')} {log.get('url')} -> {log.get('status_code')}"
                )
                break

            print(
                f"[executor] {step.step_id} FAIL attempt {attempt} "
                f"-> {log.get('status_code')} error={log.get('error')}"
            )

            # Лимит суммарных фиксов — страховка от бесконечного цикла
            if total_fixes >= MAX_TOTAL_FIXES:
                print(f"[executor] total_fixes limit ({MAX_TOTAL_FIXES}) reached, stopping")
                break

            if attempt >= MAX_STEP_ATTEMPTS:
                break

            # Вызов агента-стабилизатора (Принцип 3.4)
            fixed_step, fix = stabilize_step(current_step, ep, log)

            if fixed_step is None:
                print(f"[executor] {step.step_id} stabilizer returned no fix, stopping")
                break

            current_step = fixed_step
            total_fixes += 1
            flow_card.stabilization_log.append({
                "step_id": step.step_id,
                "attempt": attempt,
                "reasoning": fix.reasoning,
                "fixes": [f.model_dump() for f in fix.fixes],
            })

        final_results.append(log)  # финальная (последняя) попытка шага

        if not step_passed:
            break  # Шаг не прошёл — дальнейшие шаги бессмысленны

    # is_stabilized = все ШАГИ прошли (финальная попытка каждого шага)
    all_passed = all(l["passed"] for l in final_results)
    if all_passed:
        flow_card.is_stabilized = True

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
