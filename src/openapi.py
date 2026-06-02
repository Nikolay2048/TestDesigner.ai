from __future__ import annotations

from pathlib import Path
from typing import Any

from domain import ApiOperation
from io_utils import load_yaml


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def load_openapi_operations(path: str) -> list[ApiOperation]:
    spec_path = Path(path)
    spec = _resolve_refs(load_yaml(spec_path), spec_path)
    operations: list[ApiOperation] = []

    for api_path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operations.append(
                ApiOperation(
                    method=method.upper(),
                    path=api_path,
                    operation_id=operation.get("operationId") or _operation_id(method, api_path),
                    summary=operation.get("summary", ""),
                    request_schema=_request_schema(operation),
                    response_schemas=_response_schemas(operation),
                    response_statuses=list((operation.get("responses") or {}).keys()),
                )
            )
    return operations


def _resolve_refs(value: Any, current_file: Path) -> Any:
    if isinstance(value, list):
        return [_resolve_refs(item, current_file) for item in value]
    if not isinstance(value, dict):
        return value

    ref = value.get("$ref")
    if isinstance(ref, str) and ref and len(value) == 1:
        target_file, pointer = _split_ref(ref, current_file)
        target = load_yaml(target_file)
        for part in pointer:
            target = target[part]
        return _resolve_refs(target, target_file)

    return {key: _resolve_refs(item, current_file) for key, item in value.items()}


def _split_ref(ref: str, current_file: Path) -> tuple[Path, list[str]]:
    file_part, _, pointer = ref.partition("#")
    if file_part:
        target_file = (current_file.parent / file_part).resolve()
    else:
        target_file = current_file
    pointer_parts = [part for part in pointer.split("/") if part]
    return target_file, pointer_parts


def _request_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    body = operation.get("requestBody") or {}
    content = body.get("content") or {}
    json_content = content.get("application/json") or {}
    schema = json_content.get("schema")
    return schema if isinstance(schema, dict) else None


def _response_schemas(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for status, response in (operation.get("responses") or {}).items():
        if not isinstance(response, dict):
            continue
        content = response.get("content") or {}
        json_content = content.get("application/json") or {}
        schema = json_content.get("schema")
        if isinstance(schema, dict):
            schemas[str(status)] = schema
    return schemas


def _operation_id(method: str, path: str) -> str:
    parts = [part.strip("{}") for part in path.split("/") if part]
    return "_".join([method.lower(), *parts])
