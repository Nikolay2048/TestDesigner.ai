"""
Stabilizer agent — вызывается ТОЛЬКО когда один шаг упал.

Принцип 3.2: одна узкая задача — предложить фикс значений.
Принцип 3.4: не свободный ReAct. Код передаёт конкретный упавший шаг,
             агент возвращает конкретный список правок, код применяет их.
Принцип 3.5: Python проверяет, что fix ссылается только на реальные поля.
"""

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.llm import create_llm
from src.models.flow import ScenarioStep, VariableBinding, VarSource

MAX_LLM_RETRIES = 2


# ─────────────────────────── Модели вывода LLM ─────────────────────────────

class InputFix(BaseModel):
    name: str = Field(description="Field name from current inputs to change")
    new_source: str = Field(
        description="New source: 'static' or 'generated'"
    )
    new_value: str | None = Field(
        None, description="New literal value (when new_source='static')"
    )
    new_generator: str | None = Field(
        None, description="Generator name (when new_source='generated')"
    )
    explanation: str = Field(description="Why this value fixes the problem")


class StepFix(BaseModel):
    reasoning: str = Field(
        description="Why the step failed (cite the error message and constraint violated)"
    )
    fixes: list[InputFix] = Field(
        description="Fields to change. Empty list if the failure cannot be fixed by changing data."
    )
    unfixable: bool = Field(
        False,
        description="True if the failure is not a data problem "
                    "(suspected server bug, auth issue, etc.)"
    )


# ─────────────────────────── Промпт ────────────────────────────────────────

SYSTEM_STABILIZE = """\
You are a test data fixer for a REST API scenario.

A single step FAILED. Your ONLY task: propose new values for input fields
so the step succeeds on the next attempt.

RULES:
1. Read the error response to understand what constraint was violated.
2. Only suggest values that are explicitly listed in the ALLOWED VALUES / enum.
3. Only change fields that are the root cause. Keep other fields as-is.
4. Do NOT change source="from_step" or source="env" fields — only "static" or "generated".
5. If the error is not a data problem (server bug, auth error, network), set unfixable=true.
"""

HUMAN_STABILIZE = """\
== ENDPOINT AND CONSTRAINTS ==
{endpoint_spec}

== FAILED REQUEST ==
{method} {url}
Query params: {query_params}
Body: {request_body}

== ERROR RESPONSE ==
Status: {status_code}
{error_body}

== CURRENT INPUT VALUES ==
{current_inputs}

Propose fixes for the fields that violated constraints.
Only change static/generated fields. Do not touch from_step or env fields.
"""


# ─────────────────────── Вспомогательные ───────────────────────────────────

def _spec_with_constraints(ep: dict) -> str:
    """Компактная спека с упором на constraints — именно то, что нужно при фиксе."""
    lines = [f"{ep['method']} {ep['path']}", ""]

    constraints = ep.get("constraints", {})

    params = ep.get("query_params", []) + [
        {"name": f, "required": True} for f in ep.get("required_fields", [])
    ]
    if ep.get("path_params"):
        params += ep["path_params"]

    for p in params:
        name = p["name"] if isinstance(p, dict) else p
        c = constraints.get(name, {})
        parts = [f"  {name}"]
        if c.get("enum"):
            parts.append(f"ALLOWED VALUES: {c['enum']}")
        if "minimum" in c:
            parts.append(f"min={c['minimum']}")
        if "maximum" in c:
            parts.append(f"max={c['maximum']}")
        if "maxLength" in c:
            parts.append(f"maxLength={c['maxLength']}")
        lines.append("  ".join(parts))

    return "\n".join(lines)


def _format_inputs(step: ScenarioStep) -> str:
    lines = []
    for inp in step.inputs:
        if inp.source == VarSource.STATIC:
            lines.append(f"  {inp.name}: static={inp.value!r}")
        elif inp.source == VarSource.GENERATED:
            lines.append(f"  {inp.name}: generated({inp.generator})")
        elif inp.source == VarSource.FROM_STEP:
            lines.append(f"  {inp.name}: from_step={inp.source_ref}.{inp.source_field}  [DO NOT CHANGE]")
        elif inp.source == VarSource.ENV:
            lines.append(f"  {inp.name}: env={inp.value!r}  [DO NOT CHANGE]")
    return "\n".join(lines)


def _apply_fix(step: ScenarioStep, fix: StepFix) -> ScenarioStep:
    """Применяет фикс — возвращает новый ScenarioStep с изменёнными inputs."""
    fix_map = {f.name: f for f in fix.fixes}
    new_inputs = []
    for inp in step.inputs:
        f = fix_map.get(inp.name)
        if f is None:
            new_inputs.append(inp)
        else:
            try:
                new_source = VarSource(f.new_source)
            except ValueError:
                # LLM сгенерировал невалидный source (например "generated(random_string)")
                # Пробуем вытащить базовый source из строки вида "generated(...)"
                raw = (f.new_source or "").split("(")[0].strip()
                try:
                    new_source = VarSource(raw)
                except ValueError:
                    new_source = VarSource.STATIC
            new_inputs.append(inp.model_copy(update={
                "source": new_source,
                "value": f.new_value,
                "generator": f.new_generator,
            }))
    return step.model_copy(update={"inputs": new_inputs})


# ─────────────────────────── Публичная функция ─────────────────────────────

def stabilize_step(
    step: ScenarioStep,
    ep: dict,
    failed_log: dict,
) -> tuple["ScenarioStep | None", "StepFix | None"]:
    """
    Вызывает LLM с узкой задачей: предложи фикс для упавшего шага.

    Returns:
        (fixed_step, fix)  — если фикс предложен и валиден
        (None, fix)        — если unfixable или фикс невалиден
        (None, None)       — если LLM вообще не ответил
    """
    llm = create_llm()
    chain = (
        ChatPromptTemplate.from_messages([
            ("system", SYSTEM_STABILIZE),
            ("human", HUMAN_STABILIZE),
        ])
        | llm.with_structured_output(StepFix)
    )

    fix: StepFix | None = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            fix = chain.invoke({
                "endpoint_spec": _spec_with_constraints(ep),
                "method": failed_log.get("method", ""),
                "url": failed_log.get("url", ""),
                "query_params": failed_log.get("query_params", {}),
                "request_body": failed_log.get("request_body") or {},
                "status_code": failed_log.get("status_code", ""),
                "error_body": failed_log.get("response", {}),
                "current_inputs": _format_inputs(step),
            })
            break
        except Exception as e:
            print(f"[stabilizer] LLM attempt {attempt} failed: {e}")
    else:
        return None, None

    if fix.unfixable or not fix.fixes:
        print(f"[stabilizer] {step.step_id} unfixable: {fix.reasoning[:120]}")
        return None, fix

    # Принцип 3.5: фикс должен ссылаться только на реальные поля
    input_names = {inp.name for inp in step.inputs}
    bad = [f.name for f in fix.fixes if f.name not in input_names]
    if bad:
        print(f"[stabilizer] fix references unknown fields {bad}, ignoring")
        return None, fix

    # Принцип 3.5: не менять from_step/env поля (они нужны для cross-step связей)
    protected = {
        inp.name for inp in step.inputs
        if inp.source in (VarSource.FROM_STEP, VarSource.ENV)
    }
    overstepped = [f.name for f in fix.fixes if f.name in protected]
    if overstepped:
        print(f"[stabilizer] fix tried to change from_step/env fields {overstepped}, ignoring")
        fix.fixes = [f for f in fix.fixes if f.name not in protected]
        if not fix.fixes:
            return None, fix

    fixed_step = _apply_fix(step, fix)
    changed = [f"{f.name}={f.new_value or f.new_generator}" for f in fix.fixes]
    print(f"[stabilizer] {step.step_id} fix: {changed}")
    return fixed_step, fix
