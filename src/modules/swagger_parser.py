"""
OpenAPI 3.x specification parser with full multi-file $ref resolution.

Reads a root openapi.yaml (or .json) file and recursively resolves every
``$ref`` across all referenced YAML files.  For each API operation the parser
produces an :class:`EndpointDescriptor` containing:

* Fully-inlined schemas (no remaining ``$ref`` nodes)
* All descriptions, summaries, and comments preserved from the spec
* A concrete :class:`ExampleRequest` ready for Agent 1 to use

Typical usage::

    from src.modules.swagger_parser import SwaggerParser

    parser = SwaggerParser("data/openapi.yaml")
    endpoints = parser.parse()
    for ep in endpoints:
        print(ep.method, ep.path)
        print(ep.example_request.model_dump_json(indent=2))
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output data models
# ---------------------------------------------------------------------------

HTTP_METHOD = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
PARAM_LOCATION = Literal["query", "path", "header", "cookie"]


class ParameterDescriptor(BaseModel):
    """Describes a single API parameter (path / query / header / cookie)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Parameter name as defined in the spec.")
    location: PARAM_LOCATION = Field(description="Where the parameter appears in the request.")
    required: bool = Field(default=False)
    description: Optional[str] = Field(default=None)
    schema_: Dict[str, Any] = Field(default_factory=dict, alias="schema", description="Fully-resolved JSON Schema for the parameter.")
    example: Optional[Any] = Field(default=None, description="Concrete example value from the spec.")


class RequestBodyDescriptor(BaseModel):
    """Describes the request body of an API operation."""

    model_config = ConfigDict(populate_by_name=True)

    required: bool = Field(default=False)
    content_type: str = Field(default="application/json")
    description: Optional[str] = Field(default=None)
    schema_: Dict[str, Any] = Field(default_factory=dict, alias="schema", description="Fully-resolved JSON Schema for the body.")
    example: Optional[Any] = Field(default=None, description="Concrete example body derived from the schema.")


class ResponseDescriptor(BaseModel):
    """Describes a single response variant for an API operation."""

    model_config = ConfigDict(populate_by_name=True)

    status_code: str = Field(description="HTTP status code string, e.g. '200', '404'.")
    description: str = Field(default="")
    content_type: Optional[str] = Field(default=None)
    schema_: Optional[Dict[str, Any]] = Field(default=None, alias="schema", description="Fully-resolved JSON Schema for the response body.")
    example: Optional[Any] = Field(default=None, description="Concrete example response body.")


class ExampleRequest(BaseModel):
    """
    Ready-to-use example HTTP request for an API operation.

    All path parameters are substituted with example values from the spec.
    """

    method: str
    url: str = Field(description="Full URL with base URL and example path parameters filled in.")
    headers: Dict[str, str] = Field(default_factory=dict)
    query_params: Optional[Dict[str, Any]] = Field(default=None)
    body: Optional[Any] = Field(default=None)


class EndpointDescriptor(BaseModel):
    """
    Complete descriptor for a single API endpoint operation.

    All ``$ref`` nodes have been resolved inline; schemas are fully expanded.
    """

    method: HTTP_METHOD
    path: str = Field(description="Raw path template as defined in the spec, e.g. '/v1/bookings/{bookingId}'.")
    summary: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    operation_id: Optional[str] = Field(default=None)
    tags: List[str] = Field(default_factory=list)
    parameters: List[ParameterDescriptor] = Field(default_factory=list)
    request_body: Optional[RequestBodyDescriptor] = Field(default=None)
    responses: List[ResponseDescriptor] = Field(default_factory=list)
    example_request: ExampleRequest


class ParsedSpec(BaseModel):
    """Top-level result returned by :class:`SwaggerParser`."""

    title: str = Field(default="")
    version: str = Field(default="")
    base_url: str = Field(default="")
    endpoints: List[EndpointDescriptor] = Field(default_factory=list)

    def find_endpoint(self, method: str, path: str) -> Optional[EndpointDescriptor]:
        """Look up an endpoint by HTTP method and path template."""
        method = method.upper()
        for ep in self.endpoints:
            if ep.method == method and ep.path == path:
                return ep
        return None

    def summary_table(self) -> str:
        """Return a compact human-readable table of all parsed endpoints."""
        lines = [f"{'METHOD':<8} {'PATH':<45} OPERATION ID"]
        lines.append("-" * 80)
        for ep in self.endpoints:
            lines.append(f"{ep.method:<8} {ep.path:<45} {ep.operation_id or '-'}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_SUPPORTED_METHODS = ("get", "post", "put", "patch", "delete")


class SwaggerParser:
    """
    Parses an OpenAPI 3.x specification with full multi-file ``$ref`` resolution.

    The parser loads the root file, then lazily loads and resolves every
    referenced YAML file.  Resolved files are cached so each file is read
    only once even when referenced multiple times.

    Args:
        spec_path: Path to the root ``openapi.yaml`` (or ``.json``) file.
    """

    def __init__(self, spec_path: Union[str, Path]) -> None:
        self.spec_path = Path(spec_path).resolve()
        self._file_cache: Dict[str, Any] = {}
        self._root_raw: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> ParsedSpec:
        """
        Load the root spec file and return a :class:`ParsedSpec` with all
        endpoints fully resolved.
        """
        logger.info("SwaggerParser: loading spec from '%s'", self.spec_path)

        self._root_raw = self._load_yaml(self.spec_path)
        logger.debug("Root spec keys: %s", list(self._root_raw.keys()))

        resolved = self._resolve_refs(self._root_raw, self.spec_path)

        endpoints = self._extract_endpoints(resolved)

        info = resolved.get("info", {})
        servers = resolved.get("servers", [{}])
        base_url = servers[0].get("url", "") if servers else ""

        spec = ParsedSpec(
            title=info.get("title", ""),
            version=info.get("version", ""),
            base_url=base_url,
            endpoints=endpoints,
        )

        logger.info(
            "SwaggerParser: parsed %d endpoint(s) from '%s'",
            len(endpoints),
            self.spec_path.name,
        )
        return spec

    # ------------------------------------------------------------------
    # YAML loading
    # ------------------------------------------------------------------

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        logger.debug("Loading YAML: %s", path)
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _get_file(self, path: Path) -> Dict[str, Any]:
        """Return cached raw YAML content for *path*, loading it if needed."""
        key = str(path)
        if key not in self._file_cache:
            self._file_cache[key] = self._load_yaml(path)
        return self._file_cache[key]

    # ------------------------------------------------------------------
    # $ref resolution
    # ------------------------------------------------------------------

    def _resolve_refs(self, obj: Any, current_file: Path) -> Any:
        """
        Recursively walk *obj* and replace every ``{"$ref": "..."}`` node
        with the dereferenced content.

        Args:
            obj:          The object to process (dict / list / scalar).
            current_file: The YAML file that *obj* originated from; used to
                          resolve relative file paths in ``$ref`` strings.
        """
        if isinstance(obj, dict):
            if "$ref" in obj:
                return self._follow_ref(obj["$ref"], current_file)
            return {k: self._resolve_refs(v, current_file) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve_refs(item, current_file) for item in obj]
        return obj

    def _follow_ref(self, ref: str, current_file: Path) -> Any:
        """
        Resolve a single ``$ref`` string.

        Handles:
        * ``./paths/bookings.yaml``           — external file, no JSON Pointer
        * ``../schemas/BookingResponse.yaml`` — external file with relative path
        * ``#/components/schemas/Foo``        — same-file JSON Pointer
        * ``other.yaml#/definitions/Bar``     — external file + JSON Pointer
        """
        logger.debug("Following $ref '%s' from '%s'", ref, current_file.name)

        file_part, pointer_part = self._split_ref(ref)

        if file_part:
            target_path = (current_file.parent / file_part).resolve()
            raw = self._get_file(target_path)
            # Resolve refs inside the target file relative to *it*
            resolved = self._resolve_refs(raw, target_path)
        else:
            # Same-file reference: resolve against the root raw spec so that
            # JSON Pointer navigation works on the unresolved tree, then
            # resolve the extracted sub-tree.
            resolved = self._resolve_refs(self._root_raw, self.spec_path)

        if pointer_part:
            resolved = self._navigate_pointer(resolved, pointer_part)

        return resolved

    @staticmethod
    def _split_ref(ref: str) -> Tuple[str, str]:
        """
        Split a ``$ref`` into (file_part, pointer_part).

        Examples::
            "./foo.yaml"          → ("./foo.yaml", "")
            "#/components/Foo"    → ("", "/components/Foo")
            "foo.yaml#/bar"       → ("foo.yaml", "/bar")
        """
        if "#" in ref:
            file_part, pointer_part = ref.split("#", 1)
        else:
            file_part, pointer_part = ref, ""
        return file_part, pointer_part

    @staticmethod
    def _navigate_pointer(obj: Any, pointer: str) -> Any:
        """
        Navigate an RFC 6901 JSON Pointer within *obj*.

        Handles ``~0`` (escaped ``~``) and ``~1`` (escaped ``/``).
        """
        parts = [
            part.replace("~1", "/").replace("~0", "~")
            for part in pointer.strip("/").split("/")
            if part
        ]
        for part in parts:
            if isinstance(obj, dict):
                obj = obj[part]
            else:
                obj = obj[int(part)]
        return obj

    # ------------------------------------------------------------------
    # Endpoint extraction
    # ------------------------------------------------------------------

    def _extract_endpoints(self, spec: Dict[str, Any]) -> List[EndpointDescriptor]:
        servers = spec.get("servers", [{}])
        base_url = servers[0].get("url", "") if servers else ""
        endpoints: List[EndpointDescriptor] = []

        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                logger.warning("Skipping non-dict path item for '%s'", path)
                continue

            # Parameters defined at the path level apply to all operations
            path_level_params: List[Dict[str, Any]] = path_item.get("parameters", [])

            for method_lower in _SUPPORTED_METHODS:
                operation = path_item.get(method_lower)
                if not isinstance(operation, dict):
                    continue

                logger.debug("Extracting endpoint: %s %s", method_lower.upper(), path)
                descriptor = self._build_endpoint(
                    method=method_lower.upper(),
                    path=path,
                    operation=operation,
                    base_url=base_url,
                    path_level_params=path_level_params,
                )
                endpoints.append(descriptor)

        return endpoints

    def _build_endpoint(
        self,
        method: str,
        path: str,
        operation: Dict[str, Any],
        base_url: str,
        path_level_params: List[Dict[str, Any]],
    ) -> EndpointDescriptor:
        # Operation-level params override path-level ones with the same name+in
        merged_params = {
            (p["name"], p["in"]): p for p in path_level_params
        }
        for p in operation.get("parameters", []):
            merged_params[(p["name"], p["in"])] = p

        parameters = self._extract_parameters(list(merged_params.values()))
        request_body = self._extract_request_body(operation.get("requestBody"))
        responses = self._extract_responses(operation.get("responses", {}))
        example_req = self._build_example_request(
            method=method,
            path=path,
            base_url=base_url,
            parameters=parameters,
            request_body=request_body,
        )

        return EndpointDescriptor(
            method=method,  # type: ignore[arg-type]
            path=path,
            summary=operation.get("summary"),
            description=operation.get("description"),
            operation_id=operation.get("operationId"),
            tags=operation.get("tags", []),
            parameters=parameters,
            request_body=request_body,
            responses=responses,
            example_request=example_req,
        )

    # ------------------------------------------------------------------
    # Parameter / body / response extraction
    # ------------------------------------------------------------------

    def _extract_parameters(
        self, raw_params: List[Dict[str, Any]]
    ) -> List[ParameterDescriptor]:
        descriptors: List[ParameterDescriptor] = []
        for p in raw_params:
            schema = p.get("schema", {})
            example = p.get("example")
            if example is None:
                example = schema.get("example")
            descriptors.append(
                ParameterDescriptor(
                    name=p["name"],
                    location=p["in"],
                    required=p.get("required", False),
                    description=p.get("description"),
                    schema=schema,
                    example=example,
                )
            )
        return descriptors

    def _extract_request_body(
        self, raw_rb: Optional[Dict[str, Any]]
    ) -> Optional[RequestBodyDescriptor]:
        if not raw_rb:
            return None

        content: Dict[str, Any] = raw_rb.get("content", {})
        content_type = next(iter(content), "application/json")
        media_obj: Dict[str, Any] = content.get(content_type, {})

        schema = media_obj.get("schema", {})
        # Prefer explicit example in the media-type object, fall back to schema-derived
        example = media_obj.get("example") or self._build_schema_example(schema)

        return RequestBodyDescriptor(
            required=raw_rb.get("required", False),
            content_type=content_type,
            description=raw_rb.get("description"),
            schema=schema,
            example=example,
        )

    def _extract_responses(
        self, raw_responses: Dict[str, Any]
    ) -> List[ResponseDescriptor]:
        descriptors: List[ResponseDescriptor] = []
        for status_code, resp_data in raw_responses.items():
            if not isinstance(resp_data, dict):
                continue

            content: Dict[str, Any] = resp_data.get("content", {})
            content_type = next(iter(content), None)
            schema: Optional[Dict[str, Any]] = None
            example: Optional[Any] = None

            if content_type:
                media_obj = content[content_type]
                schema = media_obj.get("schema")
                example = media_obj.get("example") or (
                    self._build_schema_example(schema) if schema else None
                )

            descriptors.append(
                ResponseDescriptor(
                    status_code=str(status_code),
                    description=resp_data.get("description", ""),
                    content_type=content_type,
                    schema=schema,
                    example=example,
                )
            )
        return descriptors

    # ------------------------------------------------------------------
    # Schema example builder
    # ------------------------------------------------------------------

    def _build_schema_example(
        self, schema: Optional[Dict[str, Any]], _depth: int = 0
    ) -> Any:
        """
        Recursively derive a concrete example value from a JSON Schema node.

        Priority:
        1. Inline ``example`` field on the schema node
        2. Type-specific derivation (objects → recurse into properties, etc.)
        3. ``allOf`` → merge all sub-schema examples
        4. ``oneOf`` / ``anyOf`` → use the first variant

        The *_depth* guard prevents infinite recursion on pathological specs.
        """
        if not schema or not isinstance(schema, dict) or _depth > 10:
            return None

        # Highest priority: explicit example
        if "example" in schema:
            return schema["example"]

        # allOf: merge results of all sub-schemas
        if "allOf" in schema:
            merged: Dict[str, Any] = {}
            for sub in schema["allOf"]:
                sub_ex = self._build_schema_example(sub, _depth + 1)
                if isinstance(sub_ex, dict):
                    merged.update(sub_ex)
            return merged or None

        # oneOf / anyOf: take the first variant
        for combiner in ("oneOf", "anyOf"):
            variants = schema.get(combiner)
            if variants:
                return self._build_schema_example(variants[0], _depth + 1)

        schema_type = schema.get("type")

        # --- Object ---
        if schema_type == "object" or "properties" in schema:
            props = schema.get("properties", {})
            return {
                name: self._build_schema_example(prop_schema, _depth + 1)
                for name, prop_schema in props.items()
            }

        # --- Array ---
        if schema_type == "array":
            item_schema = schema.get("items", {})
            item_ex = self._build_schema_example(item_schema, _depth + 1)
            return [item_ex] if item_ex is not None else []

        # --- Scalars ---
        if schema_type == "string":
            if "enum" in schema:
                return schema["enum"][0]
            fmt = schema.get("format", "")
            if fmt == "date-time":
                return "2026-05-20T10:00:00Z"
            if fmt == "date":
                return "2026-05-20"
            if fmt == "uuid":
                return "00000000-0000-0000-0000-000000000001"
            if fmt == "email":
                return "user@example.com"
            if fmt == "uri":
                return "https://example.com"
            return schema.get("default", "string")

        if schema_type in ("integer", "number"):
            return schema.get("default", 0)

        if schema_type == "boolean":
            return schema.get("default", True)

        return None

    # ------------------------------------------------------------------
    # Example request builder
    # ------------------------------------------------------------------

    def _build_example_request(
        self,
        method: str,
        path: str,
        base_url: str,
        parameters: List[ParameterDescriptor],
        request_body: Optional[RequestBodyDescriptor],
    ) -> ExampleRequest:
        headers: Dict[str, str] = {"Accept": "application/json"}
        if request_body:
            headers["Content-Type"] = request_body.content_type

        query_params: Dict[str, Any] = {}
        filled_path = path

        for param in parameters:
            value = param.example if param.example is not None else f"<{param.name}>"
            if param.location == "header":
                headers[param.name] = str(value)
            elif param.location == "query":
                query_params[param.name] = value
            elif param.location == "path":
                filled_path = filled_path.replace(f"{{{param.name}}}", str(value))

        return ExampleRequest(
            method=method,
            url=f"{base_url}{filled_path}",
            headers=headers,
            query_params=query_params or None,
            body=request_body.example if request_body else None,
        )
