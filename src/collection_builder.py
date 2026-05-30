"""
Collection Builder — детерминированный Python-код, без LLM.

Универсальная инфраструктура для сборки Postman Collection v2.1.
Не знает о предметной области — принимает список PostmanStep и собирает JSON.

Доменная конфигурация (шаги, переменные) формируется в nodes/collection_builder.py
из данных пайплайна (stabilized_card, test_cases, endpoints).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


# ─────────────────────────── Входные данные ────────────────────────────────

@dataclass
class PostmanStep:
    """Конфигурация одного запроса в коллекции.

    name         — человекочитаемое название.
    method       — HTTP-метод.
    path         — URL-путь (Postman-переменные в формате {{var}}).
    query        — query-параметры {key: value|"{{var}}"}.
    headers      — HTTP заголовки [{key, value}, ...] (кроме Content-Type, он добавляется авто).
    body         — тело запроса (dict, будет сериализован в JSON).
    prerequest   — строки JS для pre-request script.
    tests        — строки JS для test script.
    """
    name: str
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    headers: list[dict[str, str]] = field(default_factory=list)
    body: dict[str, Any] | None = None
    prerequest: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)


# ──────────────────────────── Сборка ───────────────────────────────────────

def _url_object(path: str, query: dict[str, str], base_url_var: str = "baseUrl") -> dict:
    """Строит объект URL в формате Postman."""
    segments = [seg for seg in path.lstrip("/").split("/") if seg]
    raw = "{{" + base_url_var + "}}" + path
    if query:
        raw += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    return {
        "raw": raw,
        "host": ["{{" + base_url_var + "}}"],
        "path": segments,
        "query": [{"key": k, "value": str(v), "disabled": False} for k, v in query.items()],
    }


def _event(listen: str, lines: list[str]) -> dict:
    return {
        "listen": listen,
        "script": {"type": "text/javascript", "exec": lines},
    }


def step_to_postman_item(step: PostmanStep, base_url_var: str = "baseUrl") -> dict:
    """Конвертирует PostmanStep в item Postman Collection."""
    # OpenAPI {param} → Postman {{param}} для path-переменных, которые не были заменены
    path_postman = re.sub(r"\{(\w+)\}", r"{{\1}}", step.path)

    base_headers: list[dict] = []
    if step.body:
        base_headers.append({"key": "Content-Type", "value": "application/json"})
    base_headers.extend(step.headers)

    request: dict[str, Any] = {
        "method": step.method.upper(),
        "header": base_headers,
        "url": _url_object(path_postman, step.query, base_url_var),
    }

    if step.body is not None:
        request["body"] = {
            "mode": "raw",
            "raw": json.dumps(step.body, indent=2, ensure_ascii=False),
            "options": {"raw": {"language": "json"}},
        }

    events = []
    if step.prerequest:
        events.append(_event("prerequest", step.prerequest))
    if step.tests:
        events.append(_event("test", step.tests))

    return {
        "name": step.name,
        "event": events,
        "request": request,
        "response": [],
    }


def build_collection(
    steps: list[PostmanStep],
    name: str,
    variables: list[dict] | None = None,
    base_url_var: str = "baseUrl",
) -> dict:
    """Собирает Postman Collection v2.1 из плоского списка шагов."""
    return {
        "info": {
            "name": name,
            "schema": POSTMAN_SCHEMA,
        },
        "variable": variables or [],
        "item": [step_to_postman_item(s, base_url_var) for s in steps],
    }


def export_collection(collection: dict, output_path: Path) -> None:
    """Экспортирует коллекцию в JSON-файл для импорта в Postman."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2, ensure_ascii=False)
