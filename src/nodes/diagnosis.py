"""
Diagnosis — определяет природу каждого падения/стабилизации.

Два слоя:
  Layer 1 (КОД, дешёвый): нарушали ли исходные данные constraints из спеки?
    ДА  → TEST_DATA_ISSUE или CARD_ERROR — нормальная стабилизация.
    НЕТ → данные были валидны, но сервер отверг → КРАСНАЯ ЗОНА → Layer 2.

  Layer 2 (LLM, однопроходный, без петли):
    Данные нарушают ограничение из постановки, которого нет в схеме → SPEC_GAP.
    Данные валидны везде, сервер всё равно отверг → SUSPECTED_SERVICE_BUG.
    Недостаточно данных → UNCERTAIN.

Жёсткие правила (КОД, после LLM, Принцип 3.5):
  service_bug → needs_human=True
  confidence < 0.6 → needs_human=True
  evidence пустой → понизить confidence, needs_human=True
"""

from typing import Any

import jsonschema
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.llm import create_llm
from src.models.diagnosis import Diagnosis, DiagnosisCategory
from src.state import GraphState


# ─────────────── Модель вывода LLM (Layer 2) ───────────────────────────────

class _LLMDiagnosis(BaseModel):
    category: str = Field(
        description="One of: 'spec_gap', 'service_bug', 'uncertain'"
    )
    confidence: float = Field(
        description="0.0–1.0. Use 0.9+ only when you have a direct quote from "
                    "the scenario or schema as evidence."
    )
    evidence: list[str] = Field(
        description="Direct quotes or observations. Must not be empty to claim high confidence."
    )
    reasoning: str = Field(
        description="Brief explanation of the diagnosis."
    )


SYSTEM_DIAGNOSE = """\
You are diagnosing why a REST API rejected a valid request.

The request data already passed the endpoint's schema validation (enum, min/max).
Your task: explain WHY the server still rejected it.

CATEGORIES:
  "spec_gap"    — Data violates a constraint in the SCENARIO TEXT but not in the
                   OpenAPI schema. The schema is incomplete or scenario is stricter.
  "service_bug" — Data satisfies ALL constraints (schema + scenario), yet the server
                   rejected it. This is suspicious behavior.
  "uncertain"   — Not enough information to determine the cause.

RULES:
1. Return "service_bug" ONLY if you have clear evidence the data satisfies every
   constraint in BOTH the schema AND the scenario text.
2. Cite exact quotes from the scenario or schema as evidence items.
3. If uncertain, say "uncertain" — do not guess.
4. confidence reflects evidence quality: 0.9+ requires direct quotes.
"""

HUMAN_DIAGNOSE = """\
== SCENARIO TEXT ==
{scenario_text}

== ENDPOINT SPEC ==
{endpoint_spec}

== ORIGINAL REQUEST (passed schema validation) ==
{method} {url}
Query params: {query_params}
Body: {request_body}

== SERVER ERROR ==
Status: {status_code}
{error_body}

Why did the server reject this valid data?
"""


# ─────────────── Вспомогательные функции ───────────────────────────────────

def _get_value_from_log(log: dict, field_name: str) -> Any:
    """Ищет значение поля в query_params или request_body упавшего запроса."""
    qp = log.get("query_params") or {}
    body = log.get("request_body") or {}
    return qp.get(field_name) if field_name in qp else body.get(field_name)


def _layer1_check(
    original_log: dict,
    fixes: list[dict],
    constraints: dict,
    request_schema: dict | None = None,
) -> tuple[DiagnosisCategory, float, list[str]]:
    """
    Слой 1 — двойная проверка валидности исходных данных:

    1. JSON Schema валидация тела запроса через jsonschema (Принцип CLAUDE.md §8).
       Если тело не прошло схему → TEST_DATA_ISSUE.
    2. Constraint-проверка отдельных полей (enum/min/max/maxLength).
       Запасной вариант когда request_schema недоступна.

    Возвращает (category, confidence, evidence).
    """
    evidence: list[str] = []
    body = original_log.get("request_body") or {}

    # ── Слой 1a: полная валидация request_schema через jsonschema ─────────────
    if request_schema and body:
        try:
            jsonschema.validate(instance=body, schema=request_schema)
        except jsonschema.ValidationError as e:
            # Данные не прошли схему — нормальная стабилизация данных
            evidence.append(f"jsonschema violation: {e.message} (path: {list(e.absolute_path)})")
            return DiagnosisCategory.TEST_DATA_ISSUE, 0.95, evidence
        except jsonschema.SchemaError:
            pass  # некорректная схема — пропускаем, идём к constraints

    # ── Слой 1b: constraint-проверка по зафиксированным полям ─────────────────
    for fix in fixes:
        name = fix.get("name", "")
        original_value = _get_value_from_log(original_log, name)
        c = constraints.get(name, {})

        if not c:
            continue  # нет ограничений — не можем судить

        # Проверка enum
        if "enum" in c:
            allowed = [str(v) for v in c["enum"]]
            if str(original_value) not in allowed:
                evidence.append(
                    f"{name}={original_value!r} not in allowed values {c['enum']}"
                )
                return DiagnosisCategory.TEST_DATA_ISSUE, 0.9, evidence

        # Проверка minimum/maximum
        try:
            val_num = float(str(original_value))
            if "minimum" in c and val_num < c["minimum"]:
                evidence.append(f"{name}={original_value} < minimum {c['minimum']}")
                return DiagnosisCategory.TEST_DATA_ISSUE, 0.9, evidence
            if "maximum" in c and val_num > c["maximum"]:
                evidence.append(f"{name}={original_value} > maximum {c['maximum']}")
                return DiagnosisCategory.TEST_DATA_ISSUE, 0.9, evidence
        except (ValueError, TypeError):
            pass

        # Проверка maxLength
        if "maxLength" in c and isinstance(original_value, str):
            if len(original_value) > c["maxLength"]:
                evidence.append(
                    f"{name} length {len(original_value)} > maxLength {c['maxLength']}"
                )
                return DiagnosisCategory.TEST_DATA_ISSUE, 0.9, evidence

    # Нарушений не найдено → данные были валидны → КРАСНАЯ ЗОНА
    return DiagnosisCategory.SUSPECTED_SERVICE_BUG, 0.3, []


def _endpoint_summary(ep: dict) -> str:
    lines = [f"{ep.get('method')} {ep.get('path')}", ""]
    constraints = ep.get("constraints", {})
    for name, c in constraints.items():
        parts = [f"  {name}:"]
        if "enum" in c:
            parts.append(f"enum={c['enum']}")
        if "minimum" in c:
            parts.append(f"min={c['minimum']}")
        if "maximum" in c:
            parts.append(f"max={c['maximum']}")
        if "maxLength" in c:
            parts.append(f"maxLength={c['maxLength']}")
        if len(parts) > 1:
            lines.append(" ".join(parts))
    return "\n".join(lines)


def _layer2_llm(
    op_id: str,
    ep: dict,
    original_log: dict,
    raw_scenarios: str,
) -> _LLMDiagnosis | None:
    """Layer 2: однопроходный LLM — только для RED ZONE."""
    llm = create_llm()
    chain = (
        ChatPromptTemplate.from_messages([
            ("system", SYSTEM_DIAGNOSE),
            ("human", HUMAN_DIAGNOSE),
        ])
        | llm.with_structured_output(_LLMDiagnosis)
    )
    try:
        return chain.invoke({
            "scenario_text": raw_scenarios[:3000],  # обрезаем чтобы уместить в контекст
            "endpoint_spec": _endpoint_summary(ep),
            "method": original_log.get("method", ""),
            "url": original_log.get("url", ""),
            "query_params": original_log.get("query_params") or {},
            "request_body": original_log.get("request_body") or {},
            "status_code": original_log.get("status_code", ""),
            "error_body": original_log.get("response") or {},
        })
    except Exception as e:
        print(f"[diagnosis] layer2 LLM failed for {op_id}: {e}")
        return None


# ─────────────── Главный узел ──────────────────────────────────────────────

def diagnosis(state: GraphState) -> dict:
    exec_results: list[dict] = state.get("exec_results", [])
    stabilized_card_dict: dict = state.get("stabilized_card", {})
    endpoints: list[dict] = state.get("endpoints", [])
    raw_scenarios: str = state.get("raw_scenarios", "")

    stabilization_log: list[dict] = stabilized_card_dict.get("stabilization_log", [])

    if not exec_results or not stabilization_log:
        # Нечего диагностировать: всё прошло с первой попытки
        return {"diagnoses": [], "trace": ["diagnosis"]}

    ep_map = {ep["operation_id"]: ep for ep in endpoints}
    diagnoses: list[dict] = []

    for exec_result in exec_results:
        case_id = exec_result.get("case_id", "")
        steps_log: list[dict] = exec_result.get("steps_log", [])

        # Группируем попытки по step_id
        step_attempts: dict[str, list[dict]] = {}
        for log in steps_log:
            step_attempts.setdefault(log["step_id"], []).append(log)

        for stab_entry in stabilization_log:
            step_id = stab_entry["step_id"]
            fixes = stab_entry.get("fixes", [])
            attempts = step_attempts.get(step_id, [])

            if not attempts or not fixes:
                continue

            original_log = attempts[0]  # первая (упавшая) попытка
            op_id = original_log.get("operation_id", "")
            ep = ep_map.get(op_id, {})
            constraints = ep.get("constraints", {})

            # Что изменилось
            what_changed = {
                f["name"]: {
                    "from": _get_value_from_log(original_log, f["name"]),
                    "to": f.get("new_value") or f.get("new_generator"),
                }
                for f in fixes
            }

            # Layer 1 — jsonschema + constraint-check (Принцип CLAUDE.md §8)
            request_schema = ep.get("request_schema")
            category, confidence, evidence = _layer1_check(
                original_log, fixes, constraints, request_schema
            )

            reasoning = stab_entry.get("reasoning", "")
            needs_human = False

            if category == DiagnosisCategory.SUSPECTED_SERVICE_BUG:
                print(f"[diagnosis] {step_id} RED ZONE — calling Layer 2 LLM")
                llm_result = _layer2_llm(op_id, ep, original_log, raw_scenarios)
                if llm_result:
                    try:
                        category = DiagnosisCategory(llm_result.category)
                    except ValueError:
                        category = DiagnosisCategory.UNCERTAIN
                    confidence = llm_result.confidence
                    evidence = llm_result.evidence
                    reasoning = llm_result.reasoning

            # Жёсткие правила (Принцип 3.5)
            if category == DiagnosisCategory.SUSPECTED_SERVICE_BUG:
                needs_human = True
                print(f"[diagnosis] {step_id} SUSPECTED_SERVICE_BUG -> needs_human=True")
            if confidence < 0.6:
                needs_human = True
            if not evidence:
                confidence = min(confidence, 0.4)
                needs_human = True

            diag = Diagnosis(
                case_id=case_id,
                step_id=step_id,
                what_changed=what_changed,
                category=category,
                confidence=confidence,
                evidence=evidence,
                reasoning=reasoning,
                needs_human=needs_human,
            )
            diagnoses.append(diag.model_dump())

            cat_label = category.value if hasattr(category, "value") else str(category)
            print(
                f"[diagnosis] {step_id}: {cat_label} "
                f"(confidence={confidence:.2f}, needs_human={needs_human})"
            )

    return {"diagnoses": diagnoses, "trace": ["diagnosis"]}
