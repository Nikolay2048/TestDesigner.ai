"""
Validator — статическая проверка FlowCard до запуска Executor.

Ловит галлюцинации Scenario Analyst:
  1. operation_id существует в endpoints
  2. depends_on ссылается только на предыдущие шаги (нет циклов, нет forward-ref)
  3. from_step: source_ref — предшествующий шаг, source_field не пустой
  4. from_flow: source_ref входит в requires_flows
  5. target_location задан для каждого binding
"""

from src.state import GraphState


def _validate_flow_card(flow_card: dict, endpoints: list[dict]) -> list[str]:
    errors: list[str] = []
    valid_op_ids = {ep["operation_id"] for ep in endpoints}
    requires_flows = set(flow_card.get("requires_flows", []))
    seen_step_ids: set[str] = set()

    for step in flow_card.get("steps", []):
        step_id = step.get("step_id") or "?"
        op_id = step.get("operation_id") or ""

        if op_id not in valid_op_ids:
            errors.append(
                f"[{step_id}] operation_id {op_id!r} not found in endpoints"
            )

        for dep in step.get("depends_on", []):
            if dep == step_id:
                errors.append(f"[{step_id}] depends_on itself")
            elif dep not in seen_step_ids:
                errors.append(
                    f"[{step_id}] depends_on {dep!r} which is not a preceding step"
                )

        for binding in step.get("inputs", []):
            bname = binding.get("name") or "?"
            source = binding.get("source") or ""
            loc = binding.get("target_location") or ""

            if not loc:
                errors.append(f"[{step_id}.{bname}] target_location is missing")

            if source == "from_step":
                ref = binding.get("source_ref") or ""
                field = binding.get("source_field") or ""
                if not ref:
                    errors.append(
                        f"[{step_id}.{bname}] from_step binding missing source_ref"
                    )
                elif ref not in seen_step_ids:
                    errors.append(
                        f"[{step_id}.{bname}] from_step source_ref {ref!r} is not a preceding step"
                    )
                if not field:
                    errors.append(
                        f"[{step_id}.{bname}] from_step binding missing source_field"
                    )

            elif source == "from_flow":
                ref = binding.get("source_ref") or ""
                if not ref:
                    errors.append(
                        f"[{step_id}.{bname}] from_flow binding missing source_ref"
                    )
                elif ref not in requires_flows:
                    errors.append(
                        f"[{step_id}.{bname}] from_flow source_ref {ref!r} not in requires_flows"
                    )

        seen_step_ids.add(step_id)

    return errors


def validator(state: GraphState) -> dict:
    flow_card = state.get("flow_card", {})
    endpoints = state.get("endpoints", [])

    if not flow_card:
        return {
            "validation_errors": ["no flow_card produced by scenario_analyst"],
            "trace": ["validator"],
        }

    errors = _validate_flow_card(flow_card, endpoints)
    if errors:
        print(f"[validator] {len(errors)} error(s) found:")
        for e in errors:
            print(f"  {e}")
    else:
        print("[validator] FlowCard OK")

    return {
        "validation_errors": errors,
        "trace": ["validator"],
    }
