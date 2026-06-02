from __future__ import annotations

from domain import ApiOperation, DataBindingPlan, EndpointMappingResult, UnmappedStep
from generators import GeneratorRegistry


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


def validate_data_binding(
    data_binding: DataBindingPlan,
    operations: list[ApiOperation],
    static_test_data: dict,
    generator_registry: GeneratorRegistry,
) -> DataBindingPlan:
    """Record invalid data references without hiding the model output."""

    allowed_operations = {(operation.method, operation.path) for operation in operations}
    static_keys = set(static_test_data)
    global_risks = list(data_binding.risks)

    for step in data_binding.steps:
        operation_key = (step.operation.method, step.operation.path)
        if operation_key not in allowed_operations:
            risk = (
                f"Data binding references operation {step.operation.method} {step.operation.path}, "
                "but it is absent from OpenAPI."
            )
            step.risks.append(risk)
            global_risks.append(risk)

        for binding in step.request_bindings:
            if binding.source == "static" and binding.static_key not in static_keys:
                risk = (
                    f"Binding {binding.target} references unknown static key: "
                    f"{binding.static_key or '<empty>'}."
                )
                step.risks.append(risk)
                global_risks.append(risk)

            if binding.source == "generated":
                if not binding.generator or not generator_registry.has(binding.generator):
                    risk = (
                        f"Binding {binding.target} references unknown generator: "
                        f"{binding.generator or '<empty>'}."
                    )
                    step.risks.append(risk)
                    global_risks.append(risk)

            if binding.source == "response" and not binding.variable:
                risk = f"Binding {binding.target} uses response source without variable name."
                step.risks.append(risk)
                global_risks.append(risk)

        for extraction in step.response_extractions:
            if not extraction.json_path.startswith("$"):
                risk = (
                    f"Extraction {extraction.variable} has invalid json_path: "
                    f"{extraction.json_path}."
                )
                step.risks.append(risk)
                global_risks.append(risk)

    for missing in data_binding.missing_generators:
        missing.human_review_required = True

    data_binding.risks = global_risks
    return data_binding
