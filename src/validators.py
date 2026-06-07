from __future__ import annotations

from data_dependencies import _parameter_needs, _path_needs, _schema_needs
from domain import ApiOperation, DataBindingPlan, EndpointMappingResult, ResponseExtraction, UnmappedStep
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


def order_endpoint_mapping_by_business_steps(
    mapping: EndpointMappingResult,
    business_steps: list[str],
) -> EndpointMappingResult:
    """Keep endpoint mappings in the scenario order extracted by Documentation Analyst."""

    if not business_steps:
        return mapping

    order = {_normalize_step_text(step): index for index, step in enumerate(business_steps)}
    original_indexes = {id(item): index for index, item in enumerate(mapping.mappings)}

    def sort_key(item):
        step_order = order.get(_normalize_step_text(item.business_step))
        if step_order is None:
            return (len(order), original_indexes[id(item)])
        return (step_order, original_indexes[id(item)])

    reordered = sorted(mapping.mappings, key=sort_key)
    if reordered != mapping.mappings:
        mapping.risks.append("Endpoint mappings were reordered to match documented business step order.")
        mapping.mappings = reordered
    return mapping


def validate_data_binding(
    data_binding: DataBindingPlan,
    operations: list[ApiOperation],
    static_test_data: dict,
    generator_registry: GeneratorRegistry,
    external_context: dict | None = None,
) -> DataBindingPlan:
    """Record invalid data references without hiding the model output."""

    allowed_operations = {(operation.method, operation.path) for operation in operations}
    static_keys = set(_flatten_static_keys(static_test_data))
    external_keys = set(external_context or {})
    global_risks = list(data_binding.risks)

    for step in data_binding.steps:
        operation = next(
            (
                item
                for item in operations
                if (item.method.upper(), item.path)
                == (step.operation.method.upper(), step.operation.path)
            ),
            None,
        )
        operation_key = (step.operation.method, step.operation.path)
        if operation_key not in allowed_operations:
            risk = (
                f"Data binding references operation {step.operation.method} {step.operation.path}, "
                "but it is absent from OpenAPI."
            )
            step.risks.append(risk)
            global_risks.append(risk)

        needs_by_target = {}
        if operation:
            needs_by_target = {
                need.target: need
                for need in [
                    *_path_needs("", operation.path),
                    *_parameter_needs("", operation.request_parameters),
                    *_schema_needs("", operation.request_schema),
                ]
            }

        for binding in step.request_bindings:
            need = needs_by_target.get(binding.target)
            if need:
                binding.value_type = need.type
                binding.value_format = need.field_schema.get("format")
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

            if binding.source == "external_context":
                if not binding.variable or binding.variable not in external_keys:
                    risk = (
                        f"Binding {binding.target} references unknown external context key: "
                        f"{binding.variable or '<empty>'}."
                    )
                    step.risks.append(risk)
                    global_risks.append(risk)

        if operation:
            bound_targets = {binding.target for binding in step.request_bindings}
            for target in needs_by_target:
                if target not in bound_targets:
                    risk = f"Required request value has no binding: {step.operation.method} {step.operation.path} {target}."
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

    _reconcile_response_bindings(data_binding, global_risks)
    data_binding.risks = list(dict.fromkeys(global_risks))
    return data_binding


def _reconcile_response_bindings(
    data_binding: DataBindingPlan,
    risks: list[str],
) -> None:
    """Keep response consumers connected to a concrete earlier extraction."""

    producers: dict[str, list[tuple[int, ResponseExtraction]]] = {}
    for step_index, step in enumerate(data_binding.steps):
        for binding in step.request_bindings:
            if binding.source != "response":
                continue
            matches = [
                item
                for item in producers.get(binding.variable or "", [])
                if item[0] < step_index
            ]
            if not matches and binding.variable and binding.json_path and binding.source_step_id:
                try:
                    source_index = int(binding.source_step_id.removeprefix("s")) - 1
                except ValueError:
                    source_index = -1
                if 0 <= source_index < step_index:
                    extraction = ResponseExtraction(
                        variable=binding.variable,
                        json_path=binding.json_path,
                        scope="scenario",
                        source_step_id=f"s{source_index + 1:02d}",
                        policy="reconciled_from_response_binding",
                        reason=f"Required by step s{step_index + 1:02d} {binding.target}.",
                    )
                    data_binding.steps[source_index].response_extractions.append(extraction)
                    producers.setdefault(binding.variable, []).append((source_index, extraction))
                    matches = [(source_index, extraction)]
            if not matches:
                risks.append(
                    f"s{step_index + 1:02d} {binding.target} references response variable "
                    f"{binding.variable or '<empty>'} without an earlier extraction."
                )
                continue
            source_index, extraction = matches[-1]
            binding.source_step_id = f"s{source_index + 1:02d}"
            binding.json_path = extraction.json_path

        for extraction in step.response_extractions:
            extraction.source_step_id = f"s{step_index + 1:02d}"
            producers.setdefault(extraction.variable, []).append((step_index, extraction))


def _flatten_static_keys(value: dict, prefix: str = "") -> list[str]:
    keys: list[str] = []
    for key, item in value.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            keys.extend(_flatten_static_keys(item, full_key))
        else:
            keys.append(full_key)
    return keys


def _normalize_step_text(value: str) -> str:
    return " ".join(value.lower().split())
