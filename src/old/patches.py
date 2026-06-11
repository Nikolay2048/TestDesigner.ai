from __future__ import annotations

from data_dependencies import _parameter_needs, _path_needs, _schema_needs
from domain import ApiOperation, BindingPatch, DataBindingPlan, ResponseExtraction
from old.generators import GeneratorRegistry


def apply_binding_patch(
    plan: DataBindingPlan,
    patch: BindingPatch,
    static_test_data: dict,
    generator_registry: GeneratorRegistry,
    allowed_bindings: list[dict] | None = None,
    allowed_operations: list[ApiOperation] | None = None,
) -> BindingPatch | None:
    """Validate and apply one stabilization patch. Returns applied patch or None."""

    if patch.patch_type == "no_patch":
        return None
    if not patch.step_id:
        return None
    if patch.patch_type == "insert_operation":
        return _insert_operation(
            plan,
            patch,
            static_test_data,
            generator_registry,
            allowed_operations or [],
        )
    if allowed_bindings is not None and not _is_allowed_patch_target(patch, allowed_bindings):
        return None

    step = _find_step(plan, patch.step_id)
    if step is None:
        return None

    if patch.patch_type == "use_existing_variable":
        if not patch.target or not patch.variable:
            return None
        if not _variable_exists_before_step(plan, patch.step_id, patch.variable):
            return None
        for binding in step.request_bindings:
            if binding.target == patch.target:
                binding.source = "response"
                binding.variable = patch.variable
                binding.static_key = None
                binding.generator = None
                binding.params = {}
                binding.json_path = None
                binding.expression = None
                binding.literal = None
                binding.policy = "stabilization_use_existing_variable"
                binding.reason = patch.reason
                binding.requires_human_review = patch.requires_human_review
                return patch
        return None

    if patch.patch_type == "replace_request_binding":
        if not patch.target or not patch.new_binding:
            return None
        _validate_binding(patch.new_binding, static_test_data, generator_registry)
        for index, binding in enumerate(step.request_bindings):
            if binding.target == patch.target:
                if patch.new_binding.source == "response":
                    _ensure_response_extraction(plan, patch.step_id, patch.new_binding)
                step.request_bindings[index] = patch.new_binding
                return patch
        return None

    if patch.patch_type == "replace_generated_params":
        if not patch.target:
            return None
        for binding in step.request_bindings:
            if _targets_match(binding.target, patch.target) and binding.source == "generated":
                binding.params = _normalize_generated_params(binding.generator, patch.params)
                binding.policy = "stabilization_replace_generated_params"
                binding.reason = patch.reason
                binding.requires_human_review = patch.requires_human_review
                return patch
        return None

    if patch.patch_type == "replace_computed_expression":
        if not patch.target or not patch.expression:
            return None
        for binding in step.request_bindings:
            if binding.target == patch.target:
                binding.source = "computed"
                binding.expression = patch.expression
                binding.policy = "stabilization_replace_computed_expression"
                binding.reason = patch.reason
                binding.requires_human_review = patch.requires_human_review
                return patch
        return None

    if patch.patch_type == "add_response_extraction":
        if not patch.new_extraction:
            return None
        step.response_extractions.append(patch.new_extraction)
        return patch

    if patch.patch_type == "replace_response_extraction":
        if not patch.variable or not patch.new_extraction:
            return None
        for index, extraction in enumerate(step.response_extractions):
            if extraction.variable == patch.variable:
                step.response_extractions[index] = patch.new_extraction
                return patch
        return None

    return None


def _insert_operation(
    plan: DataBindingPlan,
    patch: BindingPatch,
    static_test_data: dict,
    generator_registry: GeneratorRegistry,
    allowed_operations: list[ApiOperation],
) -> BindingPatch | None:
    if not patch.new_step or not patch.step_id:
        return None
    try:
        insertion_index = int(_normalize_step_id(patch.step_id).removeprefix("s")) - 1
    except ValueError:
        return None
    if not 0 <= insertion_index < len(plan.steps):
        return None

    operation_key = (
        patch.new_step.operation.method.upper(),
        patch.new_step.operation.path,
    )
    operation = next(
        (
            item
            for item in allowed_operations
            if (item.method.upper(), item.path) == operation_key
        ),
        None,
    )
    if operation is None:
        raise ValueError("insert_operation must use an exact operation from OpenAPI")
    if any(
        (step.operation.method.upper(), step.operation.path) == operation_key
        for step in plan.steps
    ):
        raise ValueError("insert_operation cannot duplicate an operation already present in the plan")

    required_needs = [
        *_path_needs("inserted", operation.path),
        *_parameter_needs("inserted", operation.request_parameters),
        *_schema_needs("inserted", operation.request_schema),
    ]
    bindings_by_target = {binding.target: binding for binding in patch.new_step.request_bindings}
    missing = [need.target for need in required_needs if need.target not in bindings_by_target]
    if missing:
        raise ValueError(f"insert_operation is missing required bindings: {', '.join(missing)}")

    for binding in patch.new_step.request_bindings:
        _validate_binding(binding, static_test_data, generator_registry)
        if binding.source == "response":
            if not binding.variable or not _variable_exists_before_index(
                plan, insertion_index, binding.variable
            ):
                raise ValueError(
                    f"insert_operation references unavailable variable: {binding.variable}"
                )

    patch.requires_human_review = True
    plan.steps.insert(insertion_index, patch.new_step)
    _bind_failed_step_to_inserted_extractions(
        plan,
        insertion_index,
        patch.new_step,
    )
    return patch


def _bind_failed_step_to_inserted_extractions(
    plan: DataBindingPlan,
    insertion_index: int,
    inserted_step,
) -> None:
    failed_step_index = insertion_index + 1
    if failed_step_index >= len(plan.steps):
        return
    failed_step = plan.steps[failed_step_index]
    extraction_by_field = {
        _target_field_name(extraction.variable): extraction
        for extraction in inserted_step.response_extractions
    }
    source_step_id = f"s{insertion_index + 1:02d}"
    for binding in failed_step.request_bindings:
        extraction = extraction_by_field.get(_target_field_name(binding.target))
        if not extraction:
            continue
        binding.source = "response"
        binding.variable = extraction.variable
        binding.static_key = None
        binding.generator = None
        binding.params = {}
        binding.json_path = extraction.json_path
        binding.expression = None
        binding.literal = None
        binding.source_step_id = source_step_id
        binding.policy = "stabilization_inserted_operation"
        binding.reason = "Bound to the response of the inserted prerequisite operation."
        binding.requires_human_review = True


def _find_step(plan: DataBindingPlan, step_id: str):
    try:
        index = int(_normalize_step_id(step_id).removeprefix("s")) - 1
    except ValueError:
        return None
    if 0 <= index < len(plan.steps):
        return plan.steps[index]
    return None


def _ensure_response_extraction(plan: DataBindingPlan, consumer_step_id: str, binding) -> None:
    if not binding.variable or not binding.source_step_id or not binding.json_path:
        return
    source_step = _find_step(plan, binding.source_step_id)
    if source_step is None:
        return
    for extraction in source_step.response_extractions:
        if extraction.variable == binding.variable and extraction.json_path == binding.json_path:
            return
    source_step.response_extractions.append(
        ResponseExtraction(
            variable=binding.variable,
            json_path=binding.json_path,
            scope="scenario",
            source_step_id=binding.source_step_id,
            policy="stabilization_required_by_replaced_binding",
            required=True,
            reason=f"Required for {consumer_step_id} {binding.target}.",
        )
    )


def _is_allowed_patch_target(patch: BindingPatch, allowed_bindings: list[dict]) -> bool:
    """Keep LLM fixes scoped to fields named by the diagnostician."""

    if patch.patch_type == "add_response_extraction":
        patch_step_id = _normalize_step_id(patch.step_id)
        return any(_normalize_step_id(item.get("step_id")) == patch_step_id for item in allowed_bindings)

    patch_step_id = _normalize_step_id(patch.step_id)
    patch_target = patch.target or (patch.new_binding.target if patch.new_binding else None)
    patch_variable = patch.variable or (
        patch.new_extraction.variable if patch.new_extraction else None
    )
    for item in allowed_bindings:
        if _normalize_step_id(item.get("step_id")) != patch_step_id:
            continue
        allowed_target = item.get("target")
        allowed_variable = item.get("variable")
        if allowed_target and _targets_match(allowed_target, patch_target):
            return True
        if allowed_variable and patch_variable == allowed_variable:
            return True
    return False


def _variable_exists_before_step(plan: DataBindingPlan, step_id: str, variable: str) -> bool:
    try:
        step_index = int(_normalize_step_id(step_id).removeprefix("s")) - 1
    except ValueError:
        return False
    if step_index <= 0:
        return False
    for step in plan.steps[:step_index]:
        for extraction in step.response_extractions:
            if extraction.variable == variable:
                return True
    return False


def _variable_exists_before_index(
    plan: DataBindingPlan,
    insertion_index: int,
    variable: str,
) -> bool:
    for step in plan.steps[:insertion_index]:
        for extraction in step.response_extractions:
            if extraction.variable == variable:
                return True
    return False


def _validate_binding(binding, static_test_data: dict, generator_registry: GeneratorRegistry) -> None:
    if binding.source == "static":
        current = static_test_data
        for part in (binding.static_key or "").split("."):
            if not isinstance(current, dict) or part not in current:
                raise ValueError(f"Unknown static key: {binding.static_key}")
            current = current[part]
    if binding.source == "generated" and (not binding.generator or not generator_registry.has(binding.generator)):
        raise ValueError(f"Unknown generator: {binding.generator}")


def _normalize_step_id(step_id: str | None) -> str:
    if not step_id:
        return ""
    raw = str(step_id).strip()
    if len(raw) >= 2 and raw[0].lower() == "s" and raw[1:].isdigit():
        return f"s{int(raw[1:]):02d}"
    return raw


def _targets_match(binding_target: str | None, patch_target: str | None) -> bool:
    return _normalize_target(binding_target) == _normalize_target(patch_target)


def _normalize_target(target: str | None) -> str:
    if not target:
        return ""
    raw = str(target).strip()
    if raw.startswith("$."):
        return raw
    return f"$.{raw}"


def _target_field_name(value: str | None) -> str:
    if not value:
        return ""
    field = str(value).split(".")[-1]
    return "".join(character for character in field.casefold() if character.isalnum())


def _normalize_generated_params(generator: str | None, params: dict) -> dict:
    if generator == "random_int" and isinstance(params.get("values"), list) and len(params["values"]) == 1:
        value = params["values"][0]
        if isinstance(value, int):
            return {"min": value, "max": value}
    return params
