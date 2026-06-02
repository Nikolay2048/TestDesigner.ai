from __future__ import annotations

from domain import ApiOperation, EndpointMappingResult


def validate_endpoint_mapping(
    mapping: EndpointMappingResult,
    operations: list[ApiOperation],
) -> EndpointMappingResult:
    """Remove invented operations and record risks."""

    allowed = {(operation.method, operation.path) for operation in operations}
    global_risks = list(mapping.risks)

    for step_mapping in mapping.mappings:
        valid_operations = []
        for operation in step_mapping.operations:
            if (operation.method, operation.path) in allowed:
                valid_operations.append(operation)
            else:
                risk = (
                    f"Operation {operation.method} {operation.path} was returned by LLM "
                    "but is absent from OpenAPI."
                )
                step_mapping.risks.append(risk)
                global_risks.append(risk)
        step_mapping.operations = valid_operations
        if not valid_operations and step_mapping.business_step not in mapping.unmapped_steps:
            mapping.unmapped_steps.append(step_mapping.business_step)

    mapping.risks = global_risks
    return mapping

