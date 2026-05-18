"""Small OpenAPI 3 reader for REST contracts."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from src.testdesigner.models import EndpointSpec, OpenApiContract, Parameter, ResponseSpec

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class OpenApiReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._cache: dict[Path, dict[str, Any]] = {}

    def read(self) -> OpenApiContract:
        raw = self._resolve(self._load(self.path), self.path)
        info = raw.get("info") or {}
        servers = raw.get("servers") or []
        base_url = servers[0].get("url", "") if servers and isinstance(servers[0], dict) else ""
        endpoints: list[EndpointSpec] = []
        for path, path_item in (raw.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            common_params = path_item.get("parameters") or []
            for method, operation in path_item.items():
                if method not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                endpoints.append(self._endpoint(method.upper(), path, operation, common_params))
        return OpenApiContract(
            title=info.get("title", ""),
            version=str(info.get("version", "")),
            base_url=base_url,
            endpoints=endpoints,
        )

    def _endpoint(
        self,
        method: str,
        path: str,
        operation: dict[str, Any],
        common_params: list[dict[str, Any]],
    ) -> EndpointSpec:
        params = [self._parameter(p) for p in [*common_params, *(operation.get("parameters") or [])]]
        request_schema, request_example = self._request_body(operation.get("requestBody"))
        responses = [self._response(code, data) for code, data in (operation.get("responses") or {}).items()]
        return EndpointSpec(
            method=method,  # type: ignore[arg-type]
            path=path,
            operation_id=operation.get("operationId"),
            summary=operation.get("summary") or "",
            description=operation.get("description") or "",
            parameters=params,
            request_schema=request_schema,
            request_example=request_example,
            responses=responses,
        )

    def _parameter(self, raw: dict[str, Any]) -> Parameter:
        schema = raw.get("schema") or {}
        return Parameter(
            name=raw.get("name", ""),
            location=raw.get("in", "query"),
            required=bool(raw.get("required")),
            schema=schema,
            example=raw.get("example", schema.get("example", self._example(schema))),
            description=raw.get("description") or "",
        )

    def _request_body(self, raw: dict[str, Any] | None) -> tuple[dict[str, Any] | None, Any]:
        if not raw:
            return None, None
        media = self._json_media(raw.get("content") or {})
        schema = media.get("schema") or None
        example = media.get("example")
        if example is None and media.get("examples"):
            first = next(iter(media["examples"].values()))
            example = first.get("value") if isinstance(first, dict) else first
        if example is None:
            example = self._example(schema)
        return schema, example

    def _response(self, status: str, raw: dict[str, Any]) -> ResponseSpec:
        content = raw.get("content") or {}
        media = self._json_media(content)
        schema = media.get("schema") if media else None
        example = media.get("example") if media else None
        if example is None:
            example = self._example(schema)
        content_type = "application/json" if "application/json" in content else (next(iter(content), None) if content else None)
        return ResponseSpec(
            status_code=str(status),
            description=raw.get("description") or "",
            content_type=content_type,
            schema=schema,
            example=example,
        )

    def _resolve(self, node: Any, current_file: Path) -> Any:
        if isinstance(node, list):
            return [self._resolve(item, current_file) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" not in node:
            return {key: self._resolve(value, current_file) for key, value in node.items()}
        ref = node["$ref"]
        file_part, _, pointer = ref.partition("#")
        target_file = (current_file.parent / file_part).resolve() if file_part else current_file
        target = self._load(target_file)
        for part in pointer.lstrip("/").split("/") if pointer else []:
            part = part.replace("~1", "/").replace("~0", "~")
            target = target[int(part)] if isinstance(target, list) else target[part]
        resolved = self._resolve(copy.deepcopy(target), target_file)
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        if siblings and isinstance(resolved, dict):
            merged = dict(resolved)
            merged.update(self._resolve(siblings, current_file))
            return merged
        return resolved

    def _load(self, path: Path) -> dict[str, Any]:
        path = path.resolve()
        if path not in self._cache:
            self._cache[path] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return self._cache[path]

    @staticmethod
    def _json_media(content: dict[str, Any]) -> dict[str, Any]:
        return content.get("application/json") or next(iter(content.values()), {})

    def _example(self, schema: Any, depth: int = 0) -> Any:
        if not isinstance(schema, dict) or depth > 12:
            return None
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        if schema.get("enum"):
            return schema["enum"][0]
        for group in ("allOf", "oneOf", "anyOf"):
            if schema.get(group):
                if group == "allOf":
                    merged: dict[str, Any] = {}
                    for item in schema[group]:
                        value = self._example(item, depth + 1)
                        if isinstance(value, dict):
                            merged.update(value)
                    return merged or None
                return self._example(schema[group][0], depth + 1)
        if schema.get("type") == "object" or schema.get("properties"):
            return {k: self._example(v, depth + 1) for k, v in (schema.get("properties") or {}).items()}
        if schema.get("type") == "array":
            item = self._example(schema.get("items"), depth + 1)
            return [item] if item is not None else []
        if schema.get("type") == "integer":
            return schema.get("minimum", 1)
        if schema.get("type") == "number":
            return float(schema.get("minimum", 1.0))
        if schema.get("type") == "boolean":
            return True
        if schema.get("type") == "string":
            fmt = schema.get("format")
            if fmt == "date-time":
                return "2026-05-20T10:00:00Z"
            if fmt == "date":
                return "2026-05-20"
            return "string"
        return None
