"""
Spec Parser: парсит OpenAPI YAML-файлы в список Endpoint.

Принципы:
- Только детерминированный Python-код, без LLM.
- Резолвит $ref внутри одного файла (cross-file $ref — TODO этап 8).
- Извлекает constraints из схем параметров и тела запроса.
"""

from pathlib import Path
from typing import Any

import yaml

from src.models.spec import Endpoint

CONSTRAINT_KEYS = {
    "minimum", "maximum",
    "minLength", "maxLength",
    "enum", "format", "pattern",
    "minItems", "maxItems",
}


def _resolve_ref(ref: str, root: dict) -> Any:
    """Резолвит локальный $ref вида '#/components/schemas/Foo'."""
    if not ref.startswith("#/"):
        raise ValueError(f"Cross-file $ref not supported yet: {ref}")
    parts = ref[2:].split("/")
    node = root
    for part in parts:
        node = node[part]
    return node


def _inline(doc: Any, root: dict) -> Any:
    """Рекурсивно разворачивает все $ref в документе."""
    if isinstance(doc, dict):
        if "$ref" in doc:
            resolved = _resolve_ref(doc["$ref"], root)
            return _inline(resolved, root)
        return {k: _inline(v, root) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_inline(item, root) for item in doc]
    return doc


def _extract_constraints(schema: dict) -> dict:
    """Извлекает ограничения из свойств JSON Schema (properties → constraints)."""
    constraints = {}
    for field, fschema in schema.get("properties", {}).items():
        c = {k: v for k, v in fschema.items() if k in CONSTRAINT_KEYS}
        if c:
            constraints[field] = c
    return constraints


def _parse_operation(path: str, method: str, op: dict) -> Endpoint:
    operation_id = op.get("operationId", f"{method}_{path.replace('/', '_')}")

    params = op.get("parameters", [])
    path_params = [p for p in params if p.get("in") == "path"]
    query_params = [p for p in params if p.get("in") == "query"]

    # Тело запроса
    request_schema: dict | None = None
    required_fields: list[str] = []
    body = op.get("requestBody", {})
    if body:
        schema = body.get("content", {}).get("application/json", {}).get("schema")
        if schema:
            request_schema = schema
            required_fields = schema.get("required", [])

    # Схемы ответов по коду статуса
    response_schemas: dict = {}
    for status_code, response in op.get("responses", {}).items():
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        if schema:
            response_schemas[str(status_code)] = schema

    # Ограничения: из тела + из параметров
    constraints: dict = {}
    if request_schema:
        constraints.update(_extract_constraints(request_schema))
    for p in path_params + query_params:
        c = {k: v for k, v in p.get("schema", {}).items() if k in CONSTRAINT_KEYS}
        if c:
            constraints[p["name"]] = c

    return Endpoint(
        operation_id=operation_id,
        method=method,
        path=path,
        path_params=path_params,
        query_params=query_params,
        request_schema=request_schema,
        response_schemas=response_schemas,
        required_fields=required_fields,
        constraints=constraints,
    )


def parse_openapi_file(path: Path) -> list[Endpoint]:
    """Парсит один OpenAPI YAML-файл, возвращает список Endpoint."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    doc = _inline(raw, raw)
    endpoints = []

    for path_str, path_item in doc.get("paths", {}).items():
        for method, operation in path_item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                endpoints.append(_parse_operation(path_str, method.upper(), operation))

    return endpoints


def parse_openapi_files(paths: list[Path]) -> list[Endpoint]:
    """Парсит несколько OpenAPI YAML-файлов, объединяет все endpoints."""
    result = []
    for p in paths:
        result.extend(parse_openapi_file(p))
    return result
