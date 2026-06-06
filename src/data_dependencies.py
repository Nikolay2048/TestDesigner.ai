from __future__ import annotations

import re
from typing import Any

from domain import (
    DataBindingPlan,
    DataDependencyGraph,
    DataDependencyStep,
    DataNeed,
    DataProducer,
    DependencyResolution,
    DependencyResolutionTask,
    GenerationBindingDecision,
    GenerationBindingTask,
    OperationRef,
    ProjectState,
    RequestValueBinding,
    ResponseCandidate,
    ResponseExtraction,
    StepDataBinding,
)
from generators import GeneratorRegistry


PATH_PARAM_PATTERN = re.compile(r"{([^}]+)}")


def build_dependency_graph(state: ProjectState) -> DataDependencyGraph:
    """Extract request needs and response producers from mapped OpenAPI operations."""

    if not state.endpoint_mapping:
        return DataDependencyGraph()

    operations = {(operation.method.upper(), operation.path): operation for operation in state.operations}
    steps: list[DataDependencyStep] = []
    counter = 1

    for mapping in state.endpoint_mapping.mappings:
        for operation_ref in mapping.operations:
            operation = operations.get((operation_ref.method.upper(), operation_ref.path))
            if not operation:
                continue
            step_id = f"s{counter:02d}"
            operation_key = OperationRef(method=operation.method, path=operation.path)
            steps.append(
                DataDependencyStep(
                    step_id=step_id,
                    business_step=mapping.business_step,
                    operation=operation_key,
                    needs=[
                        *_path_needs(step_id, operation.path),
                        *_parameter_needs(step_id, operation.request_parameters),
                        *_schema_needs(step_id, operation.request_schema),
                    ],
                    produces=_schema_producers(step_id, operation_key, operation.response_schemas),
                )
            )
            counter += 1

    return DataDependencyGraph(steps=steps)


def build_dependency_resolution_tasks(graph: DataDependencyGraph) -> list[DependencyResolutionTask]:
    """Build small LLM tasks: one request need with previous response candidates."""

    previous_producers: list[DataProducer] = []
    tasks: list[DependencyResolutionTask] = []

    for step in graph.steps:
        for need in step.needs:
            candidates = [
                ResponseCandidate(
                    candidate_id=f"c_{producer.step_id}_{_safe_id(producer.json_path)}",
                    target=need.target,
                    source_step_id=producer.step_id,
                    json_path=producer.json_path,
                    type=producer.type,
                    producer_operation=producer.operation,
                    reason="Previous response field has a compatible primitive type.",
                )
                for producer in previous_producers
                if _types_compatible(need.type, producer.type)
                and _candidate_semantically_plausible(need, producer)
            ]
            if candidates:
                tasks.append(
                    DependencyResolutionTask(
                        step_id=step.step_id,
                        business_step=step.business_step,
                        operation=step.operation,
                        need=need,
                        candidates=candidates,
                    )
                )
        previous_producers.extend(step.produces)

    return tasks


def build_generation_binding_tasks(
    graph: DataDependencyGraph,
    dependency_resolutions: list[DependencyResolution],
    state: ProjectState,
    generator_registry: GeneratorRegistry,
    existing_decisions: list[GenerationBindingDecision] | None = None,
) -> list[GenerationBindingTask]:
    resolved = {(item.step_id, item.target) for item in dependency_resolutions if item.selected_candidate_id}
    already_bound = {
        (item.step_id, item.target)
        for item in (existing_decisions or [])
        if item.source not in {"missing", "unknown"}
    }
    business_context = _business_context(state)
    tasks: list[GenerationBindingTask] = []

    for step in graph.steps:
        for need in step.needs:
            if (step.step_id, need.target) in resolved or (step.step_id, need.target) in already_bound:
                continue
            tasks.append(
                GenerationBindingTask(
                    step_id=step.step_id,
                    business_step=step.business_step,
                    operation=step.operation,
                    need=need,
                    static_keys=sorted(_flatten_static_keys(state.static_test_data)),
                    external_context_keys=sorted(state.external_context.keys()),
                    available_generators=generator_registry.specs(),
                    business_context=business_context,
                )
            )

    return tasks


def build_external_context_binding_decisions(
    graph: DataDependencyGraph,
    dependency_resolutions: list[DependencyResolution],
    state: ProjectState,
) -> list[GenerationBindingDecision]:
    """Bind obvious request needs to stable dependency context without asking the LLM."""

    decisions: list[GenerationBindingDecision] = []

    for step in graph.steps:
        for need in step.needs:
            external_key = _external_context_key_for_need(need, state.external_context)
            if not external_key:
                continue
            decisions.append(
                GenerationBindingDecision(
                    step_id=need.step_id,
                    target=need.target,
                    source="external_context",
                    external_key=external_key,
                    confidence="high",
                    reason=(
                        f"Stable dependency context already provides {external_key}, "
                        f"which matches required request field {need.target}."
                    ),
                )
            )

    return decisions


def build_static_test_data_binding_decisions(
    graph: DataDependencyGraph,
    dependency_resolutions: list[DependencyResolution],
    existing_decisions: list[GenerationBindingDecision],
    state: ProjectState,
) -> list[GenerationBindingDecision]:
    """Bind obvious request needs to tester-owned constants before asking the LLM."""

    resolved = {(item.step_id, item.target) for item in dependency_resolutions if item.selected_candidate_id}
    already_bound = {
        (item.step_id, item.target)
        for item in existing_decisions
        if item.source not in {"missing", "unknown"}
    }
    decisions: list[GenerationBindingDecision] = []

    for step in graph.steps:
        for need in step.needs:
            if (step.step_id, need.target) in resolved or (step.step_id, need.target) in already_bound:
                continue
            static_key = _static_key_for_need(need, state.static_test_data)
            if not static_key:
                continue
            decisions.append(
                GenerationBindingDecision(
                    step_id=need.step_id,
                    target=need.target,
                    source="static",
                    static_key=static_key,
                    confidence="high",
                    reason=(
                        f"Tester-provided static data key {static_key} matches required "
                        f"request field {need.target}."
                    ),
                )
            )

    return decisions


def assemble_data_binding_plan(
    graph: DataDependencyGraph,
    dependency_tasks: list[DependencyResolutionTask],
    dependency_resolutions: list[DependencyResolution],
    generation_decisions: list[GenerationBindingDecision],
) -> DataBindingPlan:
    """Assemble final binding plan from graph facts and small agent decisions."""

    candidate_by_id = {
        candidate.candidate_id: candidate
        for task in dependency_tasks
        for candidate in task.candidates
    }
    resolution_by_need = {
        (resolution.step_id, resolution.target): resolution
        for resolution in dependency_resolutions
    }
    generation_by_need = {
        (decision.step_id, decision.target): decision
        for decision in generation_decisions
    }

    steps: list[StepDataBinding] = []
    risks: list[str] = []
    extractions_by_source_step: dict[str, list[ResponseExtraction]] = {}

    for step in graph.steps:
        request_bindings: list[RequestValueBinding] = []

        for need in step.needs:
            resolution = resolution_by_need.get((need.step_id, need.target))
            candidate = candidate_by_id.get(resolution.selected_candidate_id) if resolution else None
            decision = generation_by_need.get((need.step_id, need.target))

            # Stable dependency state is authoritative for an exact request need.
            # A small model may otherwise select a same-shaped ID from another resource.
            if decision and decision.source == "external_context":
                request_bindings.append(_binding_from_generation_decision(need, decision, graph))
                continue

            if candidate:
                variable = _variable_name(candidate.json_path)
                request_bindings.append(
                    RequestValueBinding(
                        target=need.target,
                        location=need.location,
                        source="response",
                        variable=variable,
                        json_path=candidate.json_path,
                        scope=_scope_for_need(need, graph),
                        source_step_id=candidate.source_step_id,
                        candidate_id=candidate.candidate_id,
                        policy="dependency_resolver_selected",
                        reason=resolution.reason,
                    )
                )
                extractions_by_source_step.setdefault(candidate.source_step_id, []).append(
                    ResponseExtraction(
                        variable=variable,
                        json_path=candidate.json_path,
                        scope="scenario",
                        source_step_id=candidate.source_step_id,
                        candidate_id=candidate.candidate_id,
                        policy="required_by_later_binding",
                        reason=f"Required for {step.step_id} {need.target}.",
                    )
                )
                continue

            if decision:
                binding = _binding_from_generation_decision(need, decision, graph)
                request_bindings.append(binding)
                if decision.source in {"missing", "unknown"}:
                    risks.append(f"{step.step_id} {need.target}: {decision.reason}")
                continue

            request_bindings.append(
                RequestValueBinding(
                    target=need.target,
                    location=need.location,
                    source="unknown",
                    scope=_scope_for_need(need, graph),
                    reason="No dependency resolution or generation decision was provided.",
                )
            )
            risks.append(f"{step.step_id} {need.target}: no binding decision.")

        steps.append(
            StepDataBinding(
                business_step=step.business_step,
                operation=step.operation,
                request_bindings=request_bindings,
            )
        )

    for step, graph_step in zip(steps, graph.steps, strict=False):
        step.response_extractions = _dedupe_extractions(
            extractions_by_source_step.get(graph_step.step_id, [])
        )

    return DataBindingPlan(steps=steps, risks=risks)


def _path_needs(step_id: str, path: str) -> list[DataNeed]:
    return [
        DataNeed(
            step_id=step_id,
            target=f"$.path.{match.group(1)}",
            location="path",
            type="string",
            required=True,
            field_schema={"type": "string"},
        )
        for match in PATH_PARAM_PATTERN.finditer(path)
    ]


def _parameter_needs(step_id: str, parameters: list[dict[str, Any]]) -> list[DataNeed]:
    needs: list[DataNeed] = []
    for parameter in parameters:
        if not parameter.get("required"):
            continue
        location = parameter.get("in")
        if location not in {"query", "header"}:
            continue
        name = parameter.get("name")
        if not name:
            continue
        schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
        needs.append(
            DataNeed(
                step_id=step_id,
                target=f"$.{location}.{name}",
                location=location,
                type=schema.get("type", "unknown"),
                required=True,
                field_schema=schema,
            )
        )
    return needs


def _schema_needs(step_id: str, schema: dict[str, Any] | None, prefix: str = "$") -> list[DataNeed]:
    if not schema or schema.get("type") != "object":
        return []

    needs: list[DataNeed] = []
    required = set(schema.get("required") or [])
    properties = schema.get("properties") or {}
    for name, child in properties.items():
        if name not in required or not isinstance(child, dict):
            continue
        target = f"{prefix}.{name}"
        if child.get("type") == "object":
            needs.extend(_schema_needs(step_id, child, target))
        else:
            needs.append(
                DataNeed(
                    step_id=step_id,
                    target=target,
                    location="body",
                    type=child.get("type", "unknown"),
                    required=True,
                    field_schema=child,
                )
            )
    return needs


def _schema_producers(
    step_id: str,
    operation: OperationRef,
    response_schemas: dict[str, dict[str, Any]],
) -> list[DataProducer]:
    success_schema = _first_success_schema(response_schemas)
    return _schema_producers_from_schema(step_id, operation, success_schema)


def _schema_producers_from_schema(
    step_id: str,
    operation: OperationRef,
    schema: dict[str, Any] | None,
    prefix: str = "$",
) -> list[DataProducer]:
    if not schema:
        return []
    schema_type = schema.get("type")
    if schema_type == "array":
        return _schema_producers_from_schema(step_id, operation, schema.get("items"), f"{prefix}[]")
    if schema_type != "object":
        return []

    producers: list[DataProducer] = []
    for name, child in (schema.get("properties") or {}).items():
        if not isinstance(child, dict):
            continue
        path = f"{prefix}.{name}"
        child_type = child.get("type", "unknown")
        if child_type == "object":
            producers.extend(_schema_producers_from_schema(step_id, operation, child, path))
        elif child_type == "array":
            producers.extend(_schema_producers_from_schema(step_id, operation, child, path))
        else:
            producers.append(
                DataProducer(
                    step_id=step_id,
                    json_path=path,
                    type=child_type,
                    field_name=name,
                    operation=operation,
                )
            )
    return producers


def _first_success_schema(response_schemas: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for status in sorted(response_schemas):
        if status.startswith("2"):
            return response_schemas[status]
    return None


def _types_compatible(need_type: str, producer_type: str) -> bool:
    if need_type == "unknown" or producer_type == "unknown":
        return True
    if need_type == producer_type:
        return True
    return need_type == "number" and producer_type == "integer"


def _candidate_semantically_plausible(need: DataNeed, producer: DataProducer) -> bool:
    need_tokens = set(_name_tokens(need.target))
    producer_tokens = set(_name_tokens(producer.field_name or producer.json_path))
    if not need_tokens or not producer_tokens:
        return True
    meaningful_overlap = (need_tokens & producer_tokens) - {"percent", "percentage"}
    if meaningful_overlap:
        return True

    operation_tokens = set(_name_tokens(producer.operation.path))
    if "id" in need_tokens and producer_tokens == {"id"} and (need_tokens - {"id"}) & operation_tokens:
        return True

    return False


def _name_tokens(value: str) -> list[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    raw_tokens = re.findall(r"[A-Za-z0-9]+", expanded)
    ignored = {"path", "body", "query", "header"}
    tokens = []
    for token in raw_tokens:
        normalized = token.casefold()
        if normalized in ignored or normalized.isdigit():
            continue
        if normalized.endswith("ies") and len(normalized) > 3:
            normalized = normalized[:-3] + "y"
        elif normalized.endswith("s") and len(normalized) > 1:
            normalized = normalized[:-1]
        tokens.append(normalized)
    return tokens


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return cleaned or "value"


def _variable_name(json_path: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", json_path) if part and part != "$"]
    if len(parts) >= 2 and parts[-1] == "id":
        return f"{parts[-2]}_id"
    return "_".join(parts[-2:] if len(parts) > 1 else parts) or "value"


def _scope_for_need(need: DataNeed, graph: DataDependencyGraph) -> str:
    if need.location == "path":
        return "scenario"
    future_uses = sum(
        1
        for step in graph.steps
        for item in step.needs
        if item.target == need.target and item.step_id != need.step_id
    )
    return "scenario" if future_uses else "step"


def _binding_from_generation_decision(
    need: DataNeed,
    decision: GenerationBindingDecision,
    graph: DataDependencyGraph,
) -> RequestValueBinding:
    source = "unknown" if decision.source == "missing" else decision.source
    return RequestValueBinding(
        target=need.target,
        location=need.location,
        source=source,
        variable=(
            _target_variable_name(need.target)
            if source == "generated"
            else decision.external_key
            if source == "external_context"
            else None
        ),
        static_key=decision.static_key,
        generator=decision.generator,
        params=decision.params,
        expression=decision.expression,
        literal=decision.literal,
        scope=_scope_for_need(need, graph),
        policy="generation_binding_selected" if source != "unknown" else "missing_or_unknown",
        requires_human_review=decision.requires_human_review or source == "unknown",
        reason=decision.reason,
    )


def _target_variable_name(target: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", target) if part and part != "$"]
    return "_".join(parts) or "generated_value"


def _external_context_key_for_need(need: DataNeed, external_context: dict[str, Any]) -> str | None:
    if not external_context:
        return None

    need_tokens = _need_tokens(need.target)
    if not need_tokens:
        return None

    normalized_need = "_".join(need_tokens)
    normalized_keys = {
        key: "_".join(_name_tokens(key))
        for key in external_context
    }

    for key, normalized_key in normalized_keys.items():
        if normalized_key == normalized_need:
            return key

    id_like = need_tokens[-1:] == ["id"]
    if id_like:
        resource_tokens = set(need_tokens[:-1])
        matches = [
            key
            for key in external_context
            if _external_id_key_matches(resource_tokens, _name_tokens(key))
        ]
        if len(matches) == 1:
            return matches[0]
        preferred = [key for key in matches if normalized_keys[key] == normalized_need]
        if len(preferred) == 1:
            return preferred[0]

    # Reuse dates and other exact business values only when all target tokens are present.
    matches = [
        key
        for key in external_context
        if set(need_tokens).issubset(set(_name_tokens(key)))
    ]
    return matches[0] if len(matches) == 1 else None


def _static_key_for_need(need: DataNeed, static_test_data: dict[str, Any]) -> str | None:
    if not static_test_data:
        return None

    static_keys = _flatten_static_keys(static_test_data)
    need_tokens = _need_tokens(need.target)
    if not need_tokens:
        return None

    normalized_need = "_".join(need_tokens)
    normalized_keys = {
        key: "_".join(_name_tokens(key))
        for key in static_keys
    }

    for key, normalized_key in normalized_keys.items():
        if normalized_key == normalized_need:
            return key

    matches = [
        key
        for key in static_keys
        if set(need_tokens).issubset(set(_name_tokens(key)))
    ]
    return matches[0] if len(matches) == 1 else None


def _flatten_static_keys(value: dict[str, Any], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for key, item in value.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            keys.extend(_flatten_static_keys(item, full_key))
        else:
            keys.append(full_key)
    return keys


def _need_tokens(target: str) -> list[str]:
    if target.startswith("$.path."):
        value = target.removeprefix("$.path.")
    else:
        value = target.removeprefix("$.")
    return _name_tokens(value)


def _external_id_key_matches(resource_tokens: set[str], key_tokens: list[str]) -> bool:
    if not resource_tokens or "id" not in key_tokens:
        return False
    key_resource_tokens = set(key_tokens) - {"id"}
    return bool(resource_tokens & key_resource_tokens)


def _dedupe_extractions(items: list[ResponseExtraction]) -> list[ResponseExtraction]:
    seen: set[tuple[str | None, str]] = set()
    deduped: list[ResponseExtraction] = []
    for item in items:
        key = (item.source_step_id, item.json_path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _business_context(state: ProjectState) -> list[str]:
    if not state.understanding:
        return []
    return [
        *state.understanding.preconditions,
        *state.understanding.business_rules,
        *state.understanding.success_criteria,
    ]
