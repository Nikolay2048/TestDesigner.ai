"""OpenAPI 3 parser with recursive local/external `$ref` resolution."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from src.testdesigner.models import (
    EndpointInfo,
    OpenApiCatalog,
    ParameterInfo,
    RequestExample,
    ResponseInfo,
)

logger = logging.getLogger(__name__)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


class OpenApiParser:
    def __init__(self, spec_path: str | Path) -> None:
        self.spec_path = Path(spec_path).resolve()
        self._raw_cache: Dict[Path, Dict[str, Any]] = {}
        self._ref_cache: Dict[Tuple[Path, str], Any] = {}

    def parse(self) -> OpenApiCatalog:
        logger.info("OpenApiParser: parse %s", self.spec_path)
        root = self._load(self.spec_path)
        resolved = self._resolve(copy.deepcopy(root), self.spec_path, [])
        info = resolved.get("info", {})
        base_url = self._base_url(resolved)
        endpoints = self._endpoints(resolved, base_url)
        logger.info("OpenApiParser: parsed %d endpoint(s)", len(endpoints))
        return OpenApiCatalog(
            title=info.get("title", ""),
            version=str(info.get("version", "")),
            base_url=base_url,
            endpoints=endpoints,
        )

    def _load(self, path: Path) -> Dict[str, Any]:
        path = path.resolve()
        if path not in self._raw_cache:
            logger.debug("OpenApiParser: read %s", path)
            with open(path, encoding="utf-8") as fh:
                self._raw_cache[path] = yaml.safe_load(fh) or {}
        return self._raw_cache[path]

    def _resolve(self, node: Any, current_file: Path, stack: List[str]) -> Any:
        if isinstance(node, list):
            return [self._resolve(item, current_file, stack) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" not in node:
            return {key: self._resolve(value, current_file, stack) for key, value in node.items()}

        ref = node["$ref"]
        resolved = self._follow_ref(ref, current_file, stack)
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        if siblings and isinstance(resolved, dict):
            merged = copy.deepcopy(resolved)
            merged.update(self._resolve(siblings, current_file, stack))
            return merged
        return resolved

    def _follow_ref(self, ref: str, current_file: Path, stack: List[str]) -> Any:
        file_part, pointer = self._split_ref(ref)
        target_file = (current_file.parent / file_part).resolve() if file_part else current_file.resolve()
        cache_key = (target_file, pointer)
        if cache_key in self._ref_cache:
            return copy.deepcopy(self._ref_cache[cache_key])

        marker = f"{target_file}#{pointer}"
        if marker in stack:
            logger.warning("OpenApiParser: circular ref %s", marker)
            return {}

        target = self._load(target_file)
        if pointer:
            target = self._json_pointer(target, pointer)
        resolved = self._resolve(copy.deepcopy(target), target_file, stack + [marker])
        self._ref_cache[cache_key] = copy.deepcopy(resolved)
        return resolved

    @staticmethod
    def _split_ref(ref: str) -> Tuple[str, str]:
        if "#" in ref:
            file_part, pointer = ref.split("#", 1)
            return file_part, pointer
        return ref, ""

    @staticmethod
    def _json_pointer(node: Any, pointer: str) -> Any:
        current = node
        for raw in pointer.lstrip("/").split("/"):
            part = raw.replace("~1", "/").replace("~0", "~")
            current = current[int(part)] if isinstance(current, list) else current[part]
        return current

    @staticmethod
    def _base_url(spec: Dict[str, Any]) -> str:
        servers = spec.get("servers") or [{}]
        return servers[0].get("url", "") if isinstance(servers[0], dict) else ""

    def _endpoints(self, spec: Dict[str, Any], base_url: str) -> List[EndpointInfo]:
        endpoints: List[EndpointInfo] = []
        for path, path_item in (spec.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            path_params = path_item.get("parameters") or []
            for method_name in HTTP_METHODS:
                operation = path_item.get(method_name)
                if isinstance(operation, dict):
                    endpoints.append(self._endpoint(method_name.upper(), path, operation, path_params, base_url))
        return endpoints

    def _endpoint(
        self,
        method: str,
        path: str,
        operation: Dict[str, Any],
        path_params: List[Dict[str, Any]],
        base_url: str,
    ) -> EndpointInfo:
        merged_params = {(p.get("name"), p.get("in")): p for p in path_params}
        for p in operation.get("parameters") or []:
            merged_params[(p.get("name"), p.get("in"))] = p
        params = [self._parameter(p) for p in merged_params.values()]
        request_schema, request_body, content_type = self._request_body(operation.get("requestBody"))
        responses = self._responses(operation.get("responses") or {})
        example = self._request_example(method, path, base_url, operation, params, request_body, content_type, responses)
        return EndpointInfo(
            method=method,  # type: ignore[arg-type]
            path=path,
            operation_id=operation.get("operationId"),
            summary=operation.get("summary") or "",
            description=operation.get("description") or "",
            tags=operation.get("tags") or [],
            parameters=params,
            request_schema=request_schema,
            request_example=example,
            responses=responses,
        )

    def _parameter(self, raw: Dict[str, Any]) -> ParameterInfo:
        schema = raw.get("schema") or {}
        example = raw.get("example", schema.get("example"))
        if example is None:
            example = self._schema_example(schema)
        return ParameterInfo(
            name=raw["name"],
            location=raw["in"],
            required=raw.get("required", False),
            description=raw.get("description") or "",
            schema=schema,
            example=example,
        )

    def _request_body(self, raw: Dict[str, Any] | None) -> tuple[Dict[str, Any] | None, Any, str]:
        if not raw:
            return None, None, "application/json"
        content = raw.get("content") or {}
        content_type = "application/json" if "application/json" in content else next(iter(content), "application/json")
        media = content.get(content_type) or {}
        schema = media.get("schema") or {}
        example = media.get("example")
        if example is None and media.get("examples"):
            first = next(iter(media["examples"].values()))
            example = first.get("value") if isinstance(first, dict) else first
        if example is None:
            example = self._schema_example(schema)
        return schema, example, content_type

    def _responses(self, raw: Dict[str, Any]) -> List[ResponseInfo]:
        responses: List[ResponseInfo] = []
        for status, response in raw.items():
            content = response.get("content") or {}
            content_type = "application/json" if "application/json" in content else next(iter(content), None)
            media = content.get(content_type) if content_type else None
            schema = media.get("schema") if isinstance(media, dict) else None
            example = media.get("example") if isinstance(media, dict) else None
            if example is None and schema:
                example = self._schema_example(schema)
            responses.append(
                ResponseInfo(
                    status_code=str(status),
                    description=response.get("description") or "",
                    content_type=content_type,
                    schema=schema,
                    example=example,
                )
            )
        return responses

    def _request_example(
        self,
        method: str,
        path: str,
        base_url: str,
        operation: Dict[str, Any],
        params: List[ParameterInfo],
        request_body: Any,
        content_type: str,
        responses: List[ResponseInfo],
    ) -> RequestExample:
        headers = {"Accept": "application/json"}
        if request_body is not None:
            headers["Content-Type"] = content_type
        query: Dict[str, Any] = {}
        path_values: Dict[str, Any] = {}
        comments = [text for text in (operation.get("summary"), operation.get("description")) if text]
        if isinstance(request_body, dict):
            comments.extend(self._schema_comments(operation.get("requestBody"), prefix="body"))
        for p in params:
            if p.description:
                comments.append(f"{p.location} {p.name}: {p.description}")
            if p.location == "query":
                query[p.name] = p.example
            elif p.location == "path":
                path_values[p.name] = p.example
            elif p.location == "header":
                headers[p.name] = p.example
        comments.extend([f"response {r.status_code}: {r.description}" for r in responses if r.description])
        return RequestExample(
            endpoint=path,
            method=method,  # type: ignore[arg-type]
            headers=headers,
            query_params=query,
            path_params=path_values,
            json_body=request_body,
            comments=comments,
        )

    def _schema_comments(self, request_body: Dict[str, Any] | None, prefix: str) -> List[str]:
        if not request_body:
            return []
        content = request_body.get("content") or {}
        media = content.get("application/json") or next(iter(content.values()), {})
        schema = media.get("schema") if isinstance(media, dict) else None
        comments: List[str] = []

        def visit(node: Any, path: str) -> None:
            if not isinstance(node, dict):
                return
            description = node.get("description") or node.get("title")
            if description:
                comments.append(f"{path}: {description}")
            for name, prop in (node.get("properties") or {}).items():
                visit(prop, f"{path}.{name}")
            if node.get("items"):
                visit(node["items"], f"{path}[]")

        visit(schema, prefix)
        return comments

    def _schema_example(self, schema: Dict[str, Any] | None, depth: int = 0) -> Any:
        if not schema or depth > 10:
            return None
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        if schema.get("enum"):
            return schema["enum"][0]
        if "allOf" in schema:
            merged: Dict[str, Any] = {}
            for item in schema["allOf"]:
                value = self._schema_example(item, depth + 1)
                if isinstance(value, dict):
                    merged.update(value)
            return merged or None
        for key in ("oneOf", "anyOf"):
            if schema.get(key):
                return self._schema_example(schema[key][0], depth + 1)
        schema_type = schema.get("type")
        if schema_type == "object" or schema.get("properties"):
            return {name: self._schema_example(prop, depth + 1) for name, prop in (schema.get("properties") or {}).items()}
        if schema_type == "array":
            item = self._schema_example(schema.get("items") or {}, depth + 1)
            return [item] if item is not None else []
        if schema_type == "string":
            fmt = schema.get("format")
            if fmt == "date-time":
                return "2026-05-20T10:00:00Z"
            if fmt == "date":
                return "2026-05-20"
            if fmt == "uuid":
                return "00000000-0000-0000-0000-000000000001"
            return "string"
        if schema_type == "integer":
            return schema.get("minimum", 1)
        if schema_type == "number":
            return float(schema.get("minimum", 1.0))
        if schema_type == "boolean":
            return True
        return None
