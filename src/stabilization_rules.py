from __future__ import annotations

import re
from typing import Any

from domain import BindingPatch, ProjectState, StabilizationDiagnosis


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
        hinted_value = _hinted_assignment(field_name, hints)
        if hinted_value is None:
            continue
        binding = next((item for item in plan_step.request_bindings if _targets_match(item.target, target)), None)
        if not binding or binding.source != "generated":
            continue
        if binding.generator == "random_int" and isinstance(hinted_value, int):
            return BindingPatch(
                patch_type="replace_generated_params",
                step_id=diagnosis.failed_step_id,
                target=target,
                params={"min": hinted_value, "max": hinted_value},
                reason=f"Server hint requires {field_name}={hinted_value}.",
                why_not_repeating_previous_fix="Deterministic server-hint patch, not an LLM retry.",
                requires_human_review=True,
            )
    return None


def _plan_step(state: ProjectState, step_id: str):
    try:
        index = int(step_id.removeprefix("s")) - 1
    except ValueError:
        return None
    if state.data_binding and 0 <= index < len(state.data_binding.steps):
        return state.data_binding.steps[index]
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
        raw_value = match.group(1)
        if re.fullmatch(r"-?\d+", raw_value):
            return int(raw_value)
        if re.fullmatch(r"-?\d+\.\d+", raw_value):
            return float(raw_value)
        return raw_value
    return None


def _field_name(target: str) -> str:
    return target.removeprefix("$.").split(".")[-1]


def _targets_match(left: str | None, right: str | None) -> bool:
    return _normalize_target(left) == _normalize_target(right)


def _normalize_target(target: str | None) -> str:
    if not target:
        return ""
    raw = target.strip()
    return raw if raw.startswith("$.") else f"$.{raw}"
