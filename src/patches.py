from __future__ import annotations

from domain import BindingPatch, DataBindingPlan
from generators import GeneratorRegistry


def apply_binding_patch(
    plan: DataBindingPlan,
    patch: BindingPatch,
    static_test_data: dict,
    generator_registry: GeneratorRegistry,
) -> BindingPatch | None:
    """Validate and apply one stabilization patch. Returns applied patch or None."""

    if patch.patch_type == "no_patch":
        return None
    if not patch.step_id:
        return None

    step = _find_step(plan, patch.step_id)
    if step is None:
        return None

    if patch.patch_type == "replace_request_binding":
        if not patch.target or not patch.new_binding:
            return None
        _validate_binding(patch.new_binding, static_test_data, generator_registry)
        for index, binding in enumerate(step.request_bindings):
            if binding.target == patch.target:
                step.request_bindings[index] = patch.new_binding
                return patch
        return None

    if patch.patch_type == "replace_generated_params":
        if not patch.target:
            return None
        for binding in step.request_bindings:
            if binding.target == patch.target and binding.source == "generated":
                binding.params = patch.params
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


def _find_step(plan: DataBindingPlan, step_id: str):
    try:
        index = int(step_id.removeprefix("s")) - 1
    except ValueError:
        return None
    if 0 <= index < len(plan.steps):
        return plan.steps[index]
    return None


def _validate_binding(binding, static_test_data: dict, generator_registry: GeneratorRegistry) -> None:
    if binding.source == "static" and binding.static_key not in static_test_data:
        raise ValueError(f"Unknown static key: {binding.static_key}")
    if binding.source == "generated" and (not binding.generator or not generator_registry.has(binding.generator)):
        raise ValueError(f"Unknown generator: {binding.generator}")
