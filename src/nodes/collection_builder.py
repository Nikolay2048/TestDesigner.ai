"""
Collection Builder — узел графа, Этап 2+.

Строит Postman-коллекцию из данных пайплайна:
  stabilized_card → папка Happy Path (все шаги FlowCard)
  test_cases      → папки по технике (Boundary, Negative, Equivalence, State Based)
  endpoints       → method/path/response specs
  CONFIG          → base_url и env-переменные коллекции

Принцип 3.1: только детерминированный Python-код, LLM не нужен.
Принцип 3.6: не знает о предметной области — работает с любыми scenarios.

Маппинг VariableBinding → Postman:
  STATIC    → literal value в теле/query
  ENV       → {{varName}} в теле/query + collection variable
  GENERATED → {{fieldName}} в теле/query; pre-request JS генерирует значение
  FROM_STEP → {{stepId__fieldPath}} в теле/query; test script предыдущего шага
              пишет переменную через pm.environment.set(...)
"""
import re
from typing import Any

from src.collection_builder import PostmanStep, step_to_postman_item
from src.config import CONFIG
from src.models.flow import ScenarioStep, VarSource, VariableBinding
from src.models.test_design import TestCase
from src.state import GraphState

POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
TECHNIQUE_ORDER = ["happy_path", "boundary", "negative", "equivalence", "state_based"]


# ─────────────────────── Variable naming ────────────────────────────────────

def _safe_varname(step_id: str, field_path: str) -> str:
    """
    Преобразует (step_id, JSONPath) в имя Postman-переменной.

    Примеры:
      ("step_01", "$.items[0].carId")       → "step_01__items__0__carId"
      ("step_02", "$.reservationDraftId")   → "step_02__reservationDraftId"
      ("step_01", "$")                      → "step_01__response"
    """
    path = field_path.lstrip("$").lstrip(".")
    if not path:
        return f"{step_id}__response"
    safe = re.sub(r"[\[\]\.]+", "__", path).strip("_")
    return f"{step_id}__{safe}"


def _jsonpath_to_js(field_path: str) -> str:
    """
    Преобразует JSONPath в JS-выражение относительно переменной _body.

    Примеры:
      "$.items[0].carId"     → "_body.items[0].carId"
      "$.reservationDraftId" → "_body.reservationDraftId"
      "$"                    → "_body"
    """
    if field_path in ("$", ""):
        return "_body"
    if field_path.startswith("$."):
        return "_body." + field_path[2:]
    return "_body." + field_path


# ─────────────────────── Pre-request scripts ────────────────────────────────

def _gen_prerequest(
    bindings: list[VariableBinding],
    generated_already: set[str],
) -> list[str]:
    """
    Генерирует JS pre-request script для GENERATED-биндингов.

    generated_already — набор имён полей, уже сгенерированных в предыдущих
    шагах того же набора запросов. Предотвращает повторную генерацию.
    """
    lines: list[str] = []
    uuid_helper_added = False

    for b in bindings:
        if b.source != VarSource.GENERATED:
            continue
        if b.name in generated_already:
            continue

        gen = b.generator or "uuid4"

        if gen in ("future_datetime", "future_datetime_start"):
            lines += [
                f"(function() {{",
                f"  const d = new Date();",
                f"  d.setDate(d.getDate() + 1);",
                f"  d.setHours(10, 0, 0, 0);",
                f"  pm.environment.set('{b.name}', d.toISOString());",
                f"}})();",
            ]
        elif gen == "future_datetime_end":
            lines += [
                f"(function() {{",
                f"  const d = new Date();",
                f"  d.setDate(d.getDate() + 2);",
                f"  d.setHours(10, 0, 0, 0);",
                f"  pm.environment.set('{b.name}', d.toISOString());",
                f"}})();",
            ]
        elif gen == "decimal_amount":
            lines += [f"pm.environment.set('{b.name}', '1499.99');"]
        elif gen == "fake_email":
            lines += [
                f"(function() {{",
                f"  const r = Math.random().toString(36).substring(2, 10);",
                f"  pm.environment.set('{b.name}', 'user_' + r + '@example.com');",
                f"}})();",
            ]
        elif gen == "random_string":
            lines += [
                f"(function() {{",
                f"  const r = Math.random().toString(36).substring(2, 12);",
                f"  pm.environment.set('{b.name}', 'test_' + r);",
                f"}})();",
            ]
        else:
            # uuid4 или неизвестный генератор → UUID v4
            if not uuid_helper_added:
                lines += [
                    "const __uuid4 = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'",
                    "  .replace(/[xy]/g, c => {",
                    "    const r = Math.random() * 16 | 0;",
                    "    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);",
                    "  });",
                ]
                uuid_helper_added = True
            lines += [f"pm.environment.set('{b.name}', __uuid4());"]

        generated_already.add(b.name)

    return lines


# ─────────────────────── Test scripts ────────────────────────────────────────

def _gen_test_script(
    step: ScenarioStep,
    expected_status: int | None,
    assertions: list[dict] | None,
) -> list[str]:
    """
    Генерирует JS test script:
      1. Проверка ожидаемого статус-кода.
      2. Извлечение полей из ответа в переменные окружения (для следующих шагов).
    """
    lines: list[str] = []

    # Статус
    if expected_status is not None:
        lines += [
            f"pm.test('Status {expected_status}', () => {{",
            f"  pm.response.to.have.status({expected_status});",
            f"}});",
        ]
    elif assertions:
        for a in assertions:
            if a.get("type") == "status_code":
                if "expected" in a:
                    s = a["expected"]
                    lines += [
                        f"pm.test('Status {s}', () => {{",
                        f"  pm.response.to.have.status({s});",
                        f"}});",
                    ]
                elif "expected_range" in a:
                    lo, hi = a["expected_range"]
                    lines += [
                        f"pm.test('Status {lo}-{hi}', () => {{",
                        f"  pm.expect(pm.response.code).to.be.within({lo}, {hi});",
                        f"}});",
                    ]

    # Извлечение produces → pm.environment.set
    if step.produces:
        lines += ["", "const _body = pm.response.json();"]
        for prod_path in step.produces:
            varname = _safe_varname(step.step_id, prod_path)
            accessor = _jsonpath_to_js(prod_path)
            lines += [
                f"try {{",
                f"  pm.environment.set('{varname}', {accessor});",
                f"}} catch(e) {{ /* field not present in this response */ }}",
            ]

    return lines


# ─────────────────────── Request building ───────────────────────────────────

def _resolve_value_for_postman(binding: VariableBinding) -> Any:
    """Преобразует VariableBinding в значение для Postman (literal или {{var}})."""
    if binding.source == VarSource.STATIC:
        return binding.value
    if binding.source == VarSource.ENV:
        return "{{" + (binding.value or "") + "}}"
    if binding.source == VarSource.GENERATED:
        return "{{" + binding.name + "}}"
    if binding.source in (VarSource.FROM_STEP, VarSource.FROM_FLOW):
        varname = _safe_varname(
            binding.source_ref or "unknown",
            binding.source_field or "$",
        )
        return "{{" + varname + "}}"
    return str(binding.value or "")


def _step_to_postman_step(
    step: ScenarioStep,
    ep: dict,
    name: str,
    expected_status: int | None = None,
    assertions: list[dict] | None = None,
    generated_already: set[str] | None = None,
) -> PostmanStep:
    """Конвертирует ScenarioStep + endpoint spec в PostmanStep."""
    if generated_already is None:
        generated_already = set()

    query: dict[str, Any] = {}
    body: dict[str, Any] = {}
    headers: list[dict[str, str]] = []
    path = ep["path"]

    for binding in step.inputs:
        loc = binding.target_location or ""
        value = _resolve_value_for_postman(binding)

        if loc.startswith("path."):
            param_name = loc[5:]
            path = path.replace(f"{{{param_name}}}", str(value))
        elif loc.startswith("query."):
            query[loc[6:]] = value
        elif loc.startswith("body."):
            body[loc[5:]] = value
        elif loc.startswith("header."):
            header_name = loc[7:]
            str_val = str(value)
            # Авто-форматирование Bearer для Authorization
            if header_name.lower() == "authorization" and not str_val.lower().startswith("bearer "):
                str_val = f"Bearer {str_val}"
            headers.append({"key": header_name, "value": str_val})

    prerequest = _gen_prerequest(step.inputs, generated_already)
    tests = _gen_test_script(step, expected_status, assertions)

    return PostmanStep(
        name=name,
        method=ep["method"],
        path=path,
        query=query,
        headers=headers,
        body=body if body else None,
        prerequest=prerequest,
        tests=tests,
    )


# ─────────────────────── Folder builders ────────────────────────────────────

def _test_case_to_folder(
    tc: TestCase,
    ep_map: dict,
    step_op_map: dict[str, str],
) -> dict | None:
    """
    Строит папку Postman для одного TestCase:
      [setup] шаги → прогон предусловий + извлечение переменных
      [target] шаг → модифицированный шаг с проверкой ожидаемого статуса
    """
    op_id = step_op_map.get(tc.target_step, "")
    ep = ep_map.get(op_id)
    if ep is None:
        return None

    items = []
    generated_already: set[str] = set()

    for setup_step in tc.setup_chain:
        setup_ep = ep_map.get(setup_step.operation_id)
        if setup_ep is None:
            continue
        ps = _step_to_postman_step(
            setup_step, setup_ep,
            name=f"[setup] {setup_step.step_id} — {setup_step.operation_id}",
            assertions=[{"type": "status_code", "expected_range": [200, 299]}],
            generated_already=generated_already,
        )
        items.append(step_to_postman_item(ps))

    target_step = ScenarioStep(
        step_id=tc.target_step,
        operation_id=op_id,
        inputs=tc.modified_inputs,
        produces=[],
    )
    ps = _step_to_postman_step(
        target_step, ep,
        name=f"[target] {op_id}",
        expected_status=tc.expected_status,
        generated_already=generated_already,
    )
    items.append(step_to_postman_item(ps))

    return {
        "name": f"[{tc.technique.value}] {tc.title}",
        "item": items,
    }


# ─────────────────────── Main node ──────────────────────────────────────────

def collection_builder(state: GraphState) -> dict:
    endpoints: list[dict] = state.get("endpoints", [])
    stabilized_card_dict: dict = state.get("stabilized_card", {})
    test_cases_raw: list[dict] = state.get("test_cases", [])

    ep_map = {ep["operation_id"]: ep for ep in endpoints}

    # step_id → operation_id из всех стабилизированных карточек (inter-flow)
    step_op_map: dict[str, str] = {}
    for card in state.get("all_stabilized_cards", []):
        for step in card.get("steps", []):
            step_op_map[step["step_id"]] = step["operation_id"]

    # ── Имя и id сценария ────────────────────────────────────────────────────
    # CONFIG.collection_name задаётся per-group в runner'е и содержит имя UC
    uc_label = CONFIG.collection_name if CONFIG.collection_name != "API Test Collection" else None
    flow_name = stabilized_card_dict.get("name", "Flow") if stabilized_card_dict else "Flow"
    flow_id   = stabilized_card_dict.get("flow_id", "flow") if stabilized_card_dict else "flow"
    # Папка сценария: "UC-имя" если задано, иначе "flow_id: flow_name"
    scenario_label = uc_label or f"{flow_id}: {flow_name}"

    # ── Папки по техникам ────────────────────────────────────────────────────
    technique_folders: dict[str, list[dict]] = {}
    skipped = 0

    for tc_dict in test_cases_raw:
        try:
            tc = TestCase(**tc_dict)
        except Exception as e:
            print(f"[collection_builder] invalid test_case, skipping: {e}")
            skipped += 1
            continue

        folder = _test_case_to_folder(tc, ep_map, step_op_map)
        if folder is None:
            skipped += 1
            continue

        tech = tc.technique.value
        technique_folders.setdefault(tech, []).append(folder)

    # Техники в фиксированном порядке — happy_path первым, остальные по порядку
    tech_subfolders: list[dict] = []
    for tech in TECHNIQUE_ORDER:
        cases = technique_folders.pop(tech, [])
        if cases:
            tech_subfolders.append({
                "name": tech.replace("_", " ").title(),
                "item": cases,
            })
    for tech, cases in technique_folders.items():
        tech_subfolders.append({"name": tech.replace("_", " ").title(), "item": cases})

    scenario_folder: dict = {
        "name": scenario_label,
        "item": tech_subfolders,
    }

    # ── Переменные коллекции ─────────────────────────────────────────────────
    variables = [{"key": "baseUrl", "value": CONFIG.base_url, "type": "string"}]
    for k, v in CONFIG.env_vars.items():
        variables.append({"key": k, "value": v, "type": "string"})

    # ── Имя коллекции ────────────────────────────────────────────────────────
    if CONFIG.collection_name != "API Test Collection":
        col_name = CONFIG.collection_name
    else:
        col_name = f"Test Collection — {flow_name}"

    collection = {
        "info": {"name": col_name, "schema": POSTMAN_SCHEMA},
        "variable": variables,
        "item": [scenario_folder] if tech_subfolders else [],
    }

    total_cases = sum(len(f["item"]) for f in tech_subfolders)
    msg = f"[collection_builder] 1 scenario folder, {len(tech_subfolders)} technique(s), {total_cases} test case(s)"
    if skipped:
        msg += f", {skipped} skipped"
    print(msg)

    return {"collection": collection, "trace": ["collection_builder"]}
