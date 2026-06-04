from __future__ import annotations

from domain import ApiOperation, OperationRef, ProjectState
from data_dependencies import PATH_PARAM_PATTERN, _name_tokens


def apply_dependency_context_to_endpoint_mapping(state: ProjectState) -> list[str]:
    """Remove setup operations that are already satisfied by stable dependency state."""

    if not state.endpoint_mapping or not state.external_context:
        return []

    operations = {(operation.method.upper(), operation.path): operation for operation in state.operations}
    notes: list[str] = []

    for mapping in state.endpoint_mapping.mappings:
        kept = []
        for operation_ref in mapping.operations:
            operation = operations.get((operation_ref.method.upper(), operation_ref.path))
            if operation and _create_operation_is_satisfied(operation, state.external_context):
                replacement = _matching_get_operation(operation, state.operations)
                if len(mapping.operations) == 1 and replacement:
                    note = (
                        f"Replaced {operation.method} {operation.path} with "
                        f"{replacement.method} {replacement.path}: stable dependency context "
                        "already provides the created resource id."
                    )
                    mapping.risks.append(note)
                    notes.append(note)
                    kept.append(OperationRef(method=replacement.method, path=replacement.path))
                    continue
                note = (
                    f"Skipped {operation.method} {operation.path}: stable dependency context "
                    "already provides the created resource id."
                )
                mapping.risks.append(note)
                notes.append(note)
                continue
            kept.append(operation_ref)

        if kept:
            mapping.operations = kept

    if notes:
        state.endpoint_mapping.risks.extend(notes)
    return notes


def _create_operation_is_satisfied(operation: ApiOperation, external_context: dict) -> bool:
    if operation.method.upper() != "POST":
        return False
    if PATH_PARAM_PATTERN.search(operation.path):
        return False
    if not _operation_produces_root_id(operation):
        return False

    resource_tokens = _resource_tokens(operation.path)
    if not resource_tokens:
        return False

    for key in external_context:
        key_tokens = set(_name_tokens(key))
        if "id" in key_tokens and bool((key_tokens - {"id"}) & set(resource_tokens)):
            return True
    return False


def _operation_produces_root_id(operation: ApiOperation) -> bool:
    for status, schema in operation.response_schemas.items():
        if not status.startswith("2") or schema.get("type") != "object":
            continue
        properties = schema.get("properties") or {}
        if "id" in properties:
            return True
    return False


def _resource_tokens(path: str) -> list[str]:
    parts = [part for part in path.split("/") if part and "{" not in part]
    if not parts:
        return []
    return _name_tokens(parts[-1])


def _matching_get_operation(create_operation: ApiOperation, operations: list[ApiOperation]) -> ApiOperation | None:
    create_parts = [part for part in create_operation.path.split("/") if part]
    if len(create_parts) != 1:
        return None
    collection = create_parts[0]
    for operation in operations:
        if operation.method.upper() != "GET":
            continue
        parts = [part for part in operation.path.split("/") if part]
        if len(parts) == 2 and parts[0] == collection and PATH_PARAM_PATTERN.fullmatch(parts[1]):
            return operation
    return None
