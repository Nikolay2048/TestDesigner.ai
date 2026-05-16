"""OpenAPI parser with multi-file reference resolution.

The parser returns endpoint descriptors that are directly useful for agents:
fully resolved schemas, request examples, response examples, parameter
metadata, and human-readable operation notes. YAML comments are not available
after normal YAML parsing, so the parser preserves OpenAPI-native comments:
summary, description, parameter descriptions, requestBody descriptions, and
response descriptions.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field

logger = logging.getLogger(__name__)

HTTP_METHOD = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
PARAM_LOCATION = Literal["query", "path", "header", "cookie"]
_SUPPORTED_METHODS = ("get", "post", "put", "patch", "delete")


class ParameterDescriptor(BaseModel):
    """Describes one OpenAPI parameter."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    location: PARAM_LOCATION = Field(alias="in")
    required: bool = False
    description: Optional[str] = None
    schema_: Dict[str, Any] = Field(default_factory=dict, alias="schema")
    example: Optional[Any] = None


class RequestBodyDescriptor(BaseModel):
    """Resolved operation request body."""

    model_config = ConfigDict(populate_by_name=True)

    required: bool = False
    content_type: str = "application/json"
    description: Optional[str] = None
    schema_: Dict[str, Any] = Field(default_factory=dict, alias="schema")
    example: Optional[Any] = None


class ResponseDescriptor(BaseModel):
    """Resolved operation response variant."""

    model_config = ConfigDict(populate_by_name=True)

    status_code: str
    description: str = ""
    content_type: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(default=None, alias="schema")
    example: Optional[Any] = None


class ExampleRequest(BaseModel):
    """Concrete request example derived from an operation."""

    method: HTTP_METHOD
    endpoint: str
    url: str
    headers: Dict[str, Any] = Field(default_factory=dict)
    query_params: Dict[str, Any] = Field(default_factory=dict)
    path_params: Dict[str, Any] = Field(default_factory=dict)
    json_body: Optional[Any] = None
    comments: List[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def body(self) -> Optional[Any]:
        """Backward-compatible alias for older demo scripts."""
        return self.json_body


class EndpointDescriptor(BaseModel):
    """Complete resolved OpenAPI operation descriptor."""

    method: HTTP_METHOD
    path: str
    summary: Optional[str] = None
    description: Optional[str] = None
    operation_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    parameters: List[ParameterDescriptor] = Field(default_factory=list)
    request_body: Optional[RequestBodyDescriptor] = None
    responses: List[ResponseDescriptor] = Field(default_factory=list)
    example_request: ExampleRequest


class ParsedSpec(BaseModel):
    """Top-level parser result."""

    title: str = ""
    version: str = ""
    base_url: str = ""
    endpoints: List[EndpointDescriptor] = Field(default_factory=list)

    def find_endpoint(self, method: str, path: str) -> Optional[EndpointDescriptor]:
        method = method.upper()
        for endpoint in self.endpoints:
            if endpoint.method == method and endpoint.path == path:
                return endpoint
        return None

    def operation_catalog(self) -> str:
        """Compact text catalog for LLM prompts."""
        lines: List[str] = []
        for ep in self.endpoints:
            body = ep.request_body.example if ep.request_body else None
            responses = ", ".join(f"{r.status_code}: {r.description}" for r in ep.responses)
            lines.extend(
                [
                    f"{ep.method} {ep.path}",
                    f"operationId: {ep.operation_id or '-'}",
                    f"summary: {ep.summary or '-'}",
                    f"description: {ep.description or '-'}",
                    f"parameters: {[p.model_dump(by_alias=True) for p in ep.parameters]}",
                    f"request_example: {body}",
                    f"responses: {responses}",
                    "",
                ]
            )
        return "\n".join(lines)

    def summary_table(self) -> str:
        lines = [f"{'METHOD':<8} {'PATH':<48} OPERATION ID", "-" * 90]
        for ep in self.endpoints:
            lines.append(f"{ep.method:<8} {ep.path:<48} {ep.operation_id or '-'}")
        return "\n".join(lines)


class SwaggerParser:
    """Parses OpenAPI 3.x YAML/JSON files with recursive `$ref` resolution."""

    def __init__(self, spec_path: Union[str, Path]) -> None:
        self.spec_path = Path(spec_path).resolve()
        self._file_cache: Dict[Path, Dict[str, Any]] = {}
        self._resolved_cache: Dict[Tuple[Path, str], Any] = {}

    def parse(self) -> ParsedSpec:
        logger.info("SwaggerParser: loading %s", self.spec_path)
        root = self._load_yaml(self.spec_path)
        resolved = self._resolve(root, self.spec_path, stack=[])
        endpoints = self._extract_endpoints(resolved)
        info = resolved.get("info", {})
        servers = resolved.get("servers") or [{}]
        base_url = servers[0].get("url", "") if isinstance(servers[0], dict) else ""
        parsed = ParsedSpec(
            title=info.get("title", ""),
            version=str(info.get("version", "")),
            base_url=base_url,
            endpoints=endpoints,
        )
        logger.info("SwaggerParser: parsed %d endpoint(s)", len(parsed.endpoints))
        return parsed

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        path = path.resolve()
        if path not in self._file_cache:
            logger.debug("SwaggerParser: reading %s", path)
            with open(path, encoding="utf-8") as fh:
                self._file_cache[path] = yaml.safe_load(fh) or {}
        return self._file_cache[path]

    def _resolve(self, obj: Any, current_file: Path, stack: List[str]) -> Any:
        if isinstance(obj, list):
            return [self._resolve(item, current_file, stack) for item in obj]
        if not isinstance(obj, dict):
            return obj

        if "$ref" in obj:
            ref = obj["$ref"]
            resolved = self._resolve_ref(ref, current_file, stack)
            siblings = {k: v for k, v in obj.items() if k != "$ref"}
            if siblings and isinstance(resolved, dict):
                merged = copy.deepcopy(resolved)
                merged.update(self._resolve(siblings, current_file, stack))
                return merged
            return resolved

        return {key: self._resolve(value, current_file, stack) for key, value in obj.items()}

    def _resolve_ref(self, ref: str, current_file: Path, stack: List[str]) -> Any:
        file_part, pointer = self._split_ref(ref)
        target_file = (current_file.parent / file_part).resolve() if file_part else current_file.resolve()
        cache_key = (target_file, pointer)
        if cache_key in self._resolved_cache:
            return copy.deepcopy(self._resolved_cache[cache_key])

        marker = f"{target_file}#{pointer}"
        if marker in stack:
            logger.warning("SwaggerParser: circular ref detected at %s", marker)
            return {}

        raw = self._load_yaml(target_file)
        target = self._navigate_pointer(raw, pointer) if pointer else raw
        resolved = self._resolve(copy.deepcopy(target), target_file, stack + [marker])
        self._resolved_cache[cache_key] = copy.deepcopy(resolved)
        return resolved

    @staticmethod
    def _split_ref(ref: str) -> Tuple[str, str]:
        if "#" in ref:
            file_part, pointer = ref.split("#", 1)
        else:
            file_part, pointer = ref, ""
        return file_part, pointer

    @staticmethod
    def _navigate_pointer(obj: Any, pointer: str) -> Any:
        if not pointer:
            return obj
        current = obj
        for raw_part in pointer.lstrip("/").split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                current = current[part]
            elif isinstance(current, list):
                current = current[int(part)]
            else:
                raise KeyError(f"Cannot navigate JSON pointer {pointer!r}")
        return current

    def _extract_endpoints(self, spec: Dict[str, Any]) -> List[EndpointDescriptor]:
        endpoints: List[EndpointDescriptor] = []
        base_url = self._base_url(spec)
        for path, path_item in (spec.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            path_params = path_item.get("parameters") or []
            for method_lower in _SUPPORTED_METHODS:
                operation = path_item.get(method_lower)
                if not isinstance(operation, dict):
                    continue
                endpoints.append(
                    self._build_endpoint(
                        method=method_lower.upper(),
                        path=path,
                        operation=operation,
                        path_parameters=path_params,
                        base_url=base_url,
                    )
                )
        return endpoints

    @staticmethod
    def _base_url(spec: Dict[str, Any]) -> str:
        servers = spec.get("servers") or [{}]
        first = servers[0] if servers else {}
        return first.get("url", "") if isinstance(first, dict) else ""

    def _build_endpoint(
        self,
        method: str,
        path: str,
        operation: Dict[str, Any],
        path_parameters: List[Dict[str, Any]],
        base_url: str,
    ) -> EndpointDescriptor:
        merged_parameters = {(p.get("name"), p.get("in")): p for p in path_parameters}
        for param in operation.get("parameters") or []:
            merged_parameters[(param.get("name"), param.get("in"))] = param

        parameters = [self._parameter(raw) for raw in merged_parameters.values()]
        request_body = self._request_body(operation.get("requestBody"))
        responses = self._responses(operation.get("responses") or {})
        example = self._example_request(
            method=method,
            path=path,
            base_url=base_url,
            operation=operation,
            parameters=parameters,
            request_body=request_body,
            responses=responses,
        )
        return EndpointDescriptor(
            method=method,  # type: ignore[arg-type]
            path=path,
            summary=operation.get("summary"),
            description=operation.get("description"),
            operation_id=operation.get("operationId"),
            tags=operation.get("tags") or [],
            parameters=parameters,
            request_body=request_body,
            responses=responses,
            example_request=example,
        )

    def _parameter(self, raw: Dict[str, Any]) -> ParameterDescriptor:
        schema = raw.get("schema") or {}
        example = raw.get("example")
        if example is None:
            example = schema.get("example")
        if example is None:
            example = self._schema_example(schema)
        return ParameterDescriptor(
            name=raw["name"],
            **{"in": raw["in"]},
            required=raw.get("required", False),
            description=raw.get("description"),
            schema=schema,
            example=example,
        )

    def _request_body(self, raw: Optional[Dict[str, Any]]) -> Optional[RequestBodyDescriptor]:
        if not raw:
            return None
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
        return RequestBodyDescriptor(
            required=raw.get("required", False),
            content_type=content_type,
            description=raw.get("description"),
            schema=schema,
            example=example,
        )

    def _responses(self, raw: Dict[str, Any]) -> List[ResponseDescriptor]:
        result: List[ResponseDescriptor] = []
        for status_code, response in raw.items():
            if not isinstance(response, dict):
                continue
            content = response.get("content") or {}
            content_type = "application/json" if "application/json" in content else next(iter(content), None)
            media = content.get(content_type) if content_type else None
            schema = media.get("schema") if isinstance(media, dict) else None
            example = None
            if isinstance(media, dict):
                example = media.get("example")
                if example is None and media.get("examples"):
                    first = next(iter(media["examples"].values()))
                    example = first.get("value") if isinstance(first, dict) else first
            if example is None and schema:
                example = self._schema_example(schema)
            result.append(
                ResponseDescriptor(
                    status_code=str(status_code),
                    description=response.get("description", ""),
                    content_type=content_type,
                    schema=schema,
                    example=example,
                )
            )
        return result

    def _example_request(
        self,
        method: str,
        path: str,
        base_url: str,
        operation: Dict[str, Any],
        parameters: List[ParameterDescriptor],
        request_body: Optional[RequestBodyDescriptor],
        responses: List[ResponseDescriptor],
    ) -> ExampleRequest:
        headers: Dict[str, Any] = {"Accept": "application/json"}
        if request_body:
            headers["Content-Type"] = request_body.content_type

        filled_path = path
        path_params: Dict[str, Any] = {}
        query_params: Dict[str, Any] = {}
        comments: List[str] = []

        for text in (operation.get("summary"), operation.get("description")):
            if text:
                comments.append(str(text))

        for param in parameters:
            value = param.example if param.example is not None else f"<{param.name}>"
            if param.description:
                comments.append(f"{param.location} {param.name}: {param.description}")
            if param.location == "path":
                path_params[param.name] = value
                filled_path = filled_path.replace(f"{{{param.name}}}", str(value))
            elif param.location == "query":
                query_params[param.name] = value
            elif param.location == "header":
                headers[param.name] = value

        if request_body and request_body.description:
            comments.append(f"requestBody: {request_body.description}")
        for response in responses:
            if response.description:
                comments.append(f"response {response.status_code}: {response.description}")

        return ExampleRequest(
            method=method,  # type: ignore[arg-type]
            endpoint=path,
            url=f"{base_url.rstrip('/')}{filled_path}",
            headers=headers,
            query_params=query_params,
            path_params=path_params,
            json_body=request_body.example if request_body else None,
            comments=comments,
        )

    def _schema_example(self, schema: Optional[Dict[str, Any]], depth: int = 0) -> Any:
        if not schema or not isinstance(schema, dict) or depth > 12:
            return None
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        if "const" in schema:
            return schema["const"]
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
            required = set(schema.get("required") or [])
            props = schema.get("properties") or {}
            result: Dict[str, Any] = {}
            for name, prop_schema in props.items():
                if required and name not in required and prop_schema.get("nullable"):
                    continue
                result[name] = self._schema_example(prop_schema, depth + 1)
            return result
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
            if fmt == "email":
                return "user@example.com"
            return "string"
        if schema_type == "integer":
            return schema.get("minimum", 1)
        if schema_type == "number":
            return float(schema.get("minimum", 1.0))
        if schema_type == "boolean":
            return True
        return None
