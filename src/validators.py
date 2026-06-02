from __future__ import annotations

from domain import ApiOperation, EndpointMappingResult, UnmappedStep


def validate_endpoint_mapping(
    mapping: EndpointMappingResult,
    operations: list[ApiOperation],
) -> EndpointMappingResult:
    """Remove invented operations and record risks."""

    allowed = {(operation.method, operation.path) for operation in operations}
    global_risks = list(mapping.risks)
    unmapped_by_step = {item.business_step: item for item in mapping.unmapped_steps}

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
        if not valid_operations and step_mapping.business_step not in unmapped_by_step:
            reason = step_mapping.reason or "No valid OpenAPI operation was mapped for this business step."
            unmapped = UnmappedStep(business_step=step_mapping.business_step, reason=reason)
            mapping.unmapped_steps.append(unmapped)
            unmapped_by_step[unmapped.business_step] = unmapped
            global_risks.append(f"Business step is unmapped: {step_mapping.business_step}")

    for unmapped in mapping.unmapped_steps:
        if not unmapped.reason.strip():
            unmapped.reason = "No reason was provided by the model."
            global_risks.append(f"Unmapped step has no reason: {unmapped.business_step}")

    mapping.risks = global_risks
    return mapping
