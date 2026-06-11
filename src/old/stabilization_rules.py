from __future__ import annotations

import re
from typing import Any

from domain import BindingPatch, ProjectState, RequestValueBinding, StabilizationDiagnosis


def patch_from_server_hint(
    state: ProjectState,
    diagnosis: StabilizationDiagnosis,
) -> BindingPatch | None:
    """Create a deterministic patch from simple server hints such as field=100."""

    if not state.stabilization or not state.stabilization.attempts or not state.data_binding:
        return None

    latest_trace = state.stabilization.attempts[-1].trace
    failed_step = next((step for step in latest_trace.steps if step.step_id == diagnosis.failed_step_id), None)
    if not failed_step:
        return None

    hints = _hint_texts(failed_step.response_body)
    if not hints:
        return None

    plan_step = _plan_step(state, diagnosis.failed_step_id)
    if not plan_step:
        return None

    for suspected in diagnosis.suspected_bindings:
        target = suspected.get("target")
        if not target:
            continue
        field_name = _field_name(target)
        alternate_patch = _alternate_response_binding_patch(
            state,
            diagnosis.failed_step_id,
            target,
            field_name,
            hints,
        )
        if alternate_patch:
            return alternate_patch

        hinted_value = _hinted_assignment(field_name, hints)
        if hinted_value is None:
            continue
        binding = next((item for item in plan_step.request_bindings if _targets_match(item.target, target)), None)
        if not binding:
            continue
        if binding.source == "generated" and binding.generator == "random_int" and isinstance(hinted_value, int):
            return BindingPatch(
                patch_type="replace_generated_params",
                step_id=diagnosis.failed_step_id,
                target=target,
                params={"min": hinted_value, "max": hinted_value},
                reason=f"Server hint requires {field_name}={hinted_value}.",
                why_not_repeating_previous_fix="Deterministic server-hint patch, not an LLM retry.",
                requires_human_review=True,
            )
        return BindingPatch(
            patch_type="replace_request_binding",
            step_id=diagnosis.failed_step_id,
            target=target,
            new_binding=RequestValueBinding(
                target=binding.target,
                location=binding.location,
                source="literal",
                literal=hinted_value,
                scope=binding.scope,
                policy="stabilization_use_server_hint_literal",
                requires_human_review=True,
                reason=f"Server hint requires {field_name}={hinted_value}.",
            ),
            reason=f"Server hint requires {field_name}={hinted_value}.",
            why_not_repeating_previous_fix="Deterministic server-hint literal patch, not an LLM retry.",
            requires_human_review=True,
        )
    return None


def _alternate_response_binding_patch(
    state: ProjectState,
    failed_step_id: str,
    target: str,
    field_name: str,
    hints: list[str],
) -> BindingPatch | None:
    hint_text = " ".join(hints).lower()
    if "another" not in hint_text and "differ" not in hint_text and "different" not in hint_text:
        return None
    if not state.stabilization or not state.stabilization.attempts or not state.data_binding:
        return None

    failed_index = _step_index(failed_step_id)
    if failed_index is None:
        return None
    failed_plan_step = _plan_step(state, failed_step_id)
    if not failed_plan_step:
        return None

    trace_steps = state.stabilization.attempts[-1].trace.steps
    current_value = _request_value(
        trace_steps[failed_index].request if failed_index < len(trace_steps) else None,
        target,
    )
    if current_value is None:
        return None

    for source_index in range(failed_index - 1, -1, -1):
        source_step = trace_steps[source_index]
        found = _find_json_field(source_step.response_body, field_name)
        if not found:
            continue
        json_path, value = found
        if value == current_value:
            continue
        source_step_id = f"s{source_index + 1:02d}"
        current_binding = next(
            (item for item in failed_plan_step.request_bindings if _targets_match(item.target, target)),
            None,
        )
        if not current_binding:
            return None
        variable = field_name
        return BindingPatch(
            patch_type="replace_request_binding",
            step_id=failed_step_id,
            target=target,
            new_binding=RequestValueBinding(
                target=current_binding.target,
                location=current_binding.location,
                source="response",
                variable=variable,
                json_path=json_path,
                scope="scenario",
                source_step_id=source_step_id,
                policy="stabilization_use_alternate_previous_response",
                requires_human_review=True,
                reason=(
                    f"Server requires {field_name} to differ from current value; "
                    f"use {json_path} from {source_step_id}."
                ),
            ),
            reason=(
                f"Server requires {field_name} to differ from current value; "
                f"use {json_path} from {source_step_id}."
            ),
            why_not_repeating_previous_fix="Deterministic alternate-value patch from previous successful response.",
            requires_human_review=True,
        )
    return None


def _plan_step(state: ProjectState, step_id: str):
    index = _step_index(step_id)
    if index is None:
        return None
    if state.data_binding and 0 <= index < len(state.data_binding.steps):
        return state.data_binding.steps[index]
    return None


def _step_index(step_id: str) -> int | None:
    try:
        return int(step_id.removeprefix("s")) - 1
    except ValueError:
        return None


def _hint_texts(value: Any) -> list[str]:
    texts = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"hint", "message"} and isinstance(child, str):
                texts.append(child)
            else:
                texts.extend(_hint_texts(child))
    elif isinstance(value, list):
        for child in value:
            texts.extend(_hint_texts(child))
    return texts


def _hinted_assignment(field_name: str, hints: list[str]) -> int | float | str | None:
    pattern = re.compile(rf"\b{re.escape(field_name)}\s*=\s*([A-Za-z0-9_.-]+)")
    for hint in hints:
        match = pattern.search(hint)
        if not match:
            continue
        raw_value = match.group(1).rstrip(".,;:")
        if re.fullmatch(r"-?\d+", raw_value):
            return int(raw_value)
        if re.fullmatch(r"-?\d+\.\d+", raw_value):
            return float(raw_value)
        return raw_value
    return None


def _field_name(target: str) -> str:
    return target.removeprefix("$.").split(".")[-1]


def _request_value(request: Any, target: str) -> Any:
    if not isinstance(request, dict):
        return None
    raw = target.removeprefix("$.")
    if raw.startswith("path."):
        return None
    if raw.startswith("query."):
        return _get_nested(request.get("query"), raw.removeprefix("query.").split("."))
    if raw.startswith("header."):
        return _get_nested(request.get("headers"), raw.removeprefix("header.").split("."))
    return _get_nested(request.get("body"), raw.split("."))


def _get_nested(value: Any, parts: list[str]) -> Any:
    current = value
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _find_json_field(value: Any, field_name: str, prefix: str = "$") -> tuple[str, Any] | None:
    if isinstance(value, dict):
        if field_name in value:
            return f"{prefix}.{field_name}", value[field_name]
        for key, child in value.items():
            found = _find_json_field(child, field_name, f"{prefix}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_json_field(child, field_name, f"{prefix}[]")
            if found:
                return found
    return None


def _targets_match(left: str | None, right: str | None) -> bool:
    return _normalize_target(left) == _normalize_target(right)


def _normalize_target(target: str | None) -> str:
    if not target:
        return ""
    raw = target.strip()
    return raw if raw.startswith("$.") else f"$.{raw}"
