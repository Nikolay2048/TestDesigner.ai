"""
Executor utilities — используется узлом executor_stabilize.

Принцип 3.4: код ведёт цикл по шагам, контекст переменных ведёт КОД.
Принцип 3.3: реальные HTTP-запросы через requests.
"""

import re
import uuid
from datetime import datetime, timedelta
from typing import Any

import requests as http

from src.models.flow import ScenarioStep, VariableBinding, VarSource


# ─────────────────────── Data generators ───────────────────────────────────

def generate_value(generator_name: str) -> str:
    """Генерирует значение по имени генератора."""
    if generator_name == "uuid4":
        return str(uuid.uuid4())
    if generator_name == "future_datetime":
        dt = (datetime.now() + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        return dt.isoformat()
    if generator_name == "future_datetime_end":
        dt = (datetime.now() + timedelta(days=2)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        return dt.isoformat()
    if generator_name == "decimal_amount":
        return "1000.00"
    if generator_name == "fake_email":
        return f"user_{uuid.uuid4().hex[:8]}@example.com"
    if generator_name == "random_string":
        return f"test_{uuid.uuid4().hex[:10]}"
    raise ValueError(f"Unknown generator: {generator_name!r}")


# ─────────────────────── JSONPath resolver ──────────────────────────────────

def resolve_jsonpath(data: Any, path: str) -> Any:
    """
    Простой JSONPath для паттернов, которые создаёт Scenario Analyst:
      $.field                — поле словаря
      $.arr[0].field         — элемент массива
      $.arr[*].field         — первый элемент (для produce-ссылок типа $.items[*])
    """
    # Убираем случайные скобки, которые иногда добавляет LLM: ($.field) → $.field
    path = path.strip().strip("()")
    if path == "$":
        return data
    if not path.startswith("$."):
        raise ValueError(f"JSONPath must start with '$.' or '$': {path!r}")

    current = data
    # Разбить "items[0].carId" на токены ["items[0]", "carId"]
    for token in path[2:].split("."):
        if not token:
            continue
        m = re.match(r'^(.+?)\[(\d+|\*)\]$', token)
        if m:
            field, idx = m.group(1), m.group(2)
            if field:
                current = current[field]
            current = current[0] if idx == "*" else current[int(idx)]
        else:
            current = current[token]
    return current


# ─────────────────────── Variable resolver ─────────────────────────────────

def resolve_binding(
    binding: VariableBinding,
    step_context: dict[str, Any],
    generated_cache: dict[str, str],
    env: dict[str, str],
) -> Any:
    """
    Разрешает одну переменную по её source.

    generated_cache — кэш сгенерированных значений по ключу 'step_id:name',
                      чтобы в рамках одного шага значение было стабильным.
    """
    source = binding.source

    if source == VarSource.STATIC:
        return binding.value

    if source == VarSource.ENV:
        key = binding.value or ""
        # Поиск нечувствительный к регистру: "PAYMENT_ID" → "paymentId"
        val = env.get(key) or env.get(key.lower()) or env.get(_to_camel(key))
        if val is None:
            raise KeyError(f"ENV variable {key!r} not found. Available: {list(env)}")
        return val

    if source == VarSource.GENERATED:
        # Ключ — имя поля, а не имя генератора.
        # Это гарантирует что dateFrom и dateTo получат разные кэш-записи,
        # даже если используют один и тот же тип генератора.
        cache_key = binding.name
        if cache_key not in generated_cache:
            generated_cache[cache_key] = generate_value(binding.generator or "uuid4")
        return generated_cache[cache_key]

    if source == VarSource.FROM_STEP:
        ref = step_context.get(binding.source_ref)
        if ref is None:
            raise KeyError(
                f"Step {binding.source_ref!r} not yet executed or produced no response"
            )
        return resolve_jsonpath(ref, binding.source_field)

    raise ValueError(f"Unsupported source: {source!r}")


def _to_camel(s: str) -> str:
    """PAYMENT_ID → paymentId."""
    parts = s.lower().split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


# ─────────────────────── Request builder ───────────────────────────────────

def build_request(
    step: ScenarioStep,
    ep: dict,
    resolved: dict[str, Any],
    base_url: str,
) -> tuple[str, str, dict, dict, dict | None]:
    """
    Возвращает (method, url, query_params, headers, body).
    body=None для запросов без тела (GET).
    """
    method = ep["method"]
    path = ep["path"]
    query_params: dict[str, Any] = {}
    headers: dict[str, str] = {}
    body: dict[str, Any] = {}

    for binding in step.inputs:
        loc = binding.target_location or ""
        value = resolved[binding.name]

        if loc.startswith("path."):
            param_name = loc[5:]
            path = path.replace(f"{{{param_name}}}", str(value))
        elif loc.startswith("query."):
            param_name = loc[6:]
            query_params[param_name] = value
        elif loc.startswith("body."):
            field_name = loc[5:]
            body[field_name] = value
        elif loc.startswith("header."):
            header_name = loc[7:]
            str_val = str(value)
            # Авто-форматирование Authorization: добавить "Bearer " если не задан
            if header_name.lower() == "authorization" and not str_val.lower().startswith("bearer "):
                str_val = f"Bearer {str_val}"
            headers[header_name] = str_val
        # Неизвестный target_location — пропускаем (покрывается Python-валидацией Phase 2)

    url = base_url.rstrip("/") + path
    return method, url, query_params, headers, body if body else None


# ─────────────────────── Step executor ─────────────────────────────────────

def execute_step(
    step: ScenarioStep,
    ep: dict,
    step_context: dict[str, Any],
    generated_cache: dict[str, str],
    env: dict[str, str],
    base_url: str,
) -> dict:
    """
    Исполняет один шаг FlowCard.
    Возвращает лог-запись: status_code, response, produces_extracted, passed.
    """
    # 1. Разрешить все переменные
    step_generated: dict[str, str] = {}  # кэш генераторов для этого шага
    resolved: dict[str, Any] = {}
    resolve_errors: list[str] = []

    for binding in step.inputs:
        try:
            resolved[binding.name] = resolve_binding(
                binding, step_context, step_generated, env
            )
        except (KeyError, ValueError, TypeError, IndexError) as e:
            resolve_errors.append(f"{binding.name}: {e}")

    if resolve_errors:
        return {
            "step_id": step.step_id,
            "operation_id": step.operation_id,
            "passed": False,
            "error": "resolve_error",
            "details": resolve_errors,
        }

    # 2. Собрать запрос
    method, url, query_params, headers, body = build_request(step, ep, resolved, base_url)

    # 3. Отправить HTTP-запрос
    try:
        resp = http.request(
            method=method,
            url=url,
            params=query_params or None,
            headers=headers or None,
            json=body,
            timeout=10,
        )
    except Exception as e:
        return {
            "step_id": step.step_id,
            "operation_id": step.operation_id,
            "method": method,
            "url": url,
            "passed": False,
            "error": "http_error",
            "details": str(e),
        }

    try:
        resp_json = resp.json()
    except Exception:
        resp_json = {"_raw": resp.text}

    passed = 200 <= resp.status_code < 300

    # 4. Извлечь produces → записать в контекст
    produces_extracted: dict[str, Any] = {}
    if passed:
        step_context[step.step_id] = resp_json
        for field_path in step.produces:
            try:
                produces_extracted[field_path] = resolve_jsonpath(resp_json, field_path)
            except Exception as e:
                produces_extracted[field_path] = f"<extract failed: {e}>"

    return {
        "step_id": step.step_id,
        "operation_id": step.operation_id,
        "method": method,
        "url": url,
        "query_params": query_params,
        "request_headers": {k: v for k, v in headers.items() if k.lower() != "authorization"},
        "request_body": body,
        "status_code": resp.status_code,
        "response": resp_json,
        "produces_extracted": produces_extracted,
        "passed": passed,
    }
