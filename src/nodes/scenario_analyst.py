"""
Scenario Analyst — два узких LLM-вызова вместо одного «умного» агента.

Принцип 3.2: узкие задачи.
  Вызов 1 — выбор операций: только список имён, никаких деталей.
  Вызов 2 — биндинг параметров: один endpoint за раз.
Принцип 3.5: Python форсит инварианты ПОСЛЕ ответа LLM.

Мотивация разбивки: 14B-модель не справляет с «выдать полный FlowCard
за один раз» — путает operation_id с HTTP-путём и галлюцинирует поля.
Два маленьких запроса надёжнее одного большого.
"""

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.config import CONFIG
from src.logger import get_logger
from src.llm import create_llm
from src.models.flow import FlowCard, ScenarioStep, VariableBinding, VarSource
from src.state import GraphState

log = get_logger("scenario_analyst")

MAX_RETRIES = 3


# ─────────────────── Фаза 1: выбор порядка операций ────────────────────────

class OperationSelection(BaseModel):
    """Минимальный вывод Phase 1 — только имена операций в нужном порядке."""
    ordered_operation_ids: list[str] = Field(
        description="Operation IDs in execution order, copied EXACTLY from the valid list."
    )

SYSTEM_SELECT = """\
Select which API operations to call for a test scenario.

RULES:
1. Return operation_ids in the order they must be called.
2. Copy each operation_id EXACTLY from the VALID OPERATIONS list — letter for letter.
3. Do NOT use HTTP methods (GET/POST), paths (/api/v1/...), or invented names.
4. Return only the names from the list, nothing else.
5. Do NOT repeat the same operation_id more than once unless the scenario explicitly calls the same endpoint twice (e.g. double-confirmation test).
"""

HUMAN_SELECT = """\
== VALID OPERATIONS — copy these names EXACTLY ==
{op_ids_list}

== SCENARIO ==
{scenario_text}

Return ordered_operation_ids: names from the list above in the order to call them.
"""


# ─────────────── Фаза 2: биндинг параметров для одного шага ────────────────

class StepInputs(BaseModel):
    """Параметры одного шага — заполняется отдельным вызовом для каждого шага."""
    inputs: list[VariableBinding]
    produces: list[str] = Field(
        default_factory=list,
        description="JSONPath fields from response that later steps will use, e.g. ['$.carId']"
    )

SYSTEM_BIND_TEMPLATE = """\
Fill in request parameters for ONE API step in a test scenario.

== VARIABLE SOURCES ==
  "generated"  — runtime value (future dates, UUIDs, amounts, strings).
                 Set generator:
                   "future_datetime"      — start date/time (pickup, dateFrom)
                   "future_datetime_end"  — end date/time (return, dateTo) — ALWAYS use for dateTo
                   "uuid4"                — UUID identifiers
                   "fake_email"           — valid email address (user@example.com format)
                   "decimal_amount"       — monetary amounts
                   "random_string"        — short random text (names, titles, messages, descriptions)
  "from_step"  — value from a previous step's JSON response.
                 Set source_ref=<step_id>, source_field="$.fieldName"
  "static"     — hardcoded constant (enum value, currency, status).
                 Set value=<literal string>
  "env"        — environment variable. Available env vars: {env_var_names}
                 Set value=<variable_name exactly as listed above>

== TARGET LOCATIONS ==
  "query.<param>"   — query string parameter
  "body.<field>"    — JSON request body field
  "path.<param>"    — URL path parameter
  "header.<name>"   — HTTP request header (e.g. "header.Authorization")

== AUTHORIZATION RULE ==
ONLY include Authorization if the endpoint spec explicitly lists it as a required header parameter.
Do NOT add Authorization to endpoints that don't have it in their spec.
When Authorization IS required:
  - If an env var provides a token (e.g. sellerToken, buyerToken, authToken): use source="env", value=<token_var_name>, target_location="header.Authorization"
  - If a previous step produced a token ($.token from login): use source="from_step", source_ref=<login_step_id>, source_field="$.token", target_location="header.Authorization"

== RULES ==
1. Cover ALL required fields from the endpoint spec — including required headers.
2. Use EXACT field names from the spec — never rename.
3. EVERY input must have target_location.
4. from_step inputs: BOTH source_ref and source_field are required.
   Copy source_ref and source_field EXACTLY from the "AVAILABLE FROM PREVIOUS STEPS" section.
   NEVER wrap JSONPath in parentheses: write "$.items[0].adId" NOT "($.items[0].adId)".
5. produces: list specific JSONPath fields later steps will need.
   Write "$.items[0].carId" not "$.items". Write "$.reservationDraftId" not "$".
6. If a field is listed in AVAILABLE FROM PREVIOUS STEPS, use source="from_step" for it.
   Do NOT regenerate a value that is already available from a previous step.
7. ENV VARS ARE NOT STATIC STRINGS. If a value name matches an entry in the available env vars list,
   ALWAYS use source="env". NEVER copy an env var name as a source="static" string value.
   WRONG: source="static", value="publishedAdId"
   CORRECT: source="env",   value="publishedAdId"
8. For fields that must be CONSISTENT across steps (e.g. password used in both register and login),
   use source="env" with the same env var name in EVERY step, NOT source="static".
   This guarantees both steps use the identical value.
"""

HUMAN_BIND = """\
== THIS STEP ==
step_id: {step_id}
operation_id: {operation_id}

== ENDPOINT SPEC ==
{endpoint_spec}

== AVAILABLE FROM PREVIOUS STEPS ==
{previous_context}

Fill in inputs and produces for this step only.
"""


# ─────────────────────── Вспомогательные ───────────────────────────────────

def _constraint_hint(c: dict) -> str:
    """Компактная подсказка о допустимых значениях из constraints."""
    if not c:
        return ""
    parts = []
    if "enum" in c:
        parts.append(f"allowed values: {c['enum']}")
    if "minimum" in c:
        parts.append(f"min={c['minimum']}")
    if "maximum" in c:
        parts.append(f"max={c['maximum']}")
    if "minLength" in c:
        parts.append(f"minLength={c['minLength']}")
    if "maxLength" in c:
        parts.append(f"maxLength={c['maxLength']}")
    return f"  [{', '.join(parts)}]" if parts else ""


def _endpoint_spec_text(ep: dict) -> str:
    """Формат включает target_location явно — модель просто копирует."""
    lines = [f"{ep['method']} {ep['path']}", ""]

    if ep.get("requires_auth"):
        lines.append("REQUIRED header: Authorization  target_location=\"header.Authorization\"")
        lines.append("")

    if ep.get("path_params"):
        lines.append("PATH parameters (use target_location='path.<name>'):")
        for p in ep["path_params"]:
            lines.append(f"  {p['name']}  target_location=\"path.{p['name']}\"")
        lines.append("")

    constraints = ep.get("constraints", {})

    if ep.get("query_params"):
        req = [p for p in ep["query_params"] if p.get("required")]
        opt = [p for p in ep["query_params"] if not p.get("required")]
        if req:
            lines.append("REQUIRED query parameters (use target_location='query.<name>'):")
            for p in req:
                c = constraints.get(p["name"], {})
                hint = _constraint_hint(c)
                lines.append(f"  {p['name']}  target_location=\"query.{p['name']}\"{hint}")
            lines.append("")
        if opt:
            lines.append("Optional query parameters:")
            for p in opt:
                c = constraints.get(p["name"], {})
                hint = _constraint_hint(c)
                lines.append(f"  {p['name']}  target_location=\"query.{p['name']}\"{hint}")
            lines.append("")

    if ep.get("required_fields"):
        lines.append("REQUIRED body fields (use target_location='body.<name>'):")
        for f in ep["required_fields"]:
            c = constraints.get(f, {})
            hint = _constraint_hint(c)
            lines.append(f"  {f}  target_location=\"body.{f}\"{hint}")
        lines.append("")

    for status, schema in ep.get("response_schemas", {}).items():
        if schema and isinstance(schema, dict):
            props = list((schema.get("properties") or {}).keys())
            if props:
                lines.append(f"Response {status} fields (use in produces as $.fieldName):")
                lines.append(f"  {props}")
                lines.append("")

    return "\n".join(lines)


def _previous_context_text(steps_so_far: list[ScenarioStep], ep_map: dict) -> str:
    if not steps_so_far:
        return "(none — this is the first step)"
    lines = []
    for step in steps_so_far:
        lines.append(f"From {step.step_id} ({step.operation_id}) you can use:")
        for field_path in step.produces:
            lines.append(f'  source="from_step", source_ref="{step.step_id}", source_field="{field_path}"')
        if not step.produces:
            lines.append("  (no fields produced)")
    return "\n".join(lines)


# ────────────────────────────── Узел ───────────────────────────────────────

def scenario_analyst(state: GraphState) -> dict:
    endpoints: list[dict] = state.get("endpoints", [])
    raw_scenarios: str = state.get("raw_scenarios", "")

    if not endpoints or not raw_scenarios.strip():
        return {"flow_card": {}, "trace": ["scenario_analyst"]}

    valid_op_ids = {ep["operation_id"] for ep in endpoints}
    ep_map = {ep["operation_id"]: ep for ep in endpoints}
    op_ids_list = "\n".join(f"  - {oid}" for oid in sorted(valid_op_ids))

    llm = create_llm()

    # ── Фаза 1: выбрать операции ────────────────────────────────────────────
    select_chain = (
        ChatPromptTemplate.from_messages([("system", SYSTEM_SELECT), ("human", HUMAN_SELECT)])
        | llm.with_structured_output(OperationSelection)
    )

    selected_ids: list[str] = []
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            selection: OperationSelection = select_chain.invoke({
                "op_ids_list": op_ids_list,
                "scenario_text": raw_scenarios,
            })
            invalid = [oid for oid in selection.ordered_operation_ids if oid not in valid_op_ids]
            if invalid:
                raise ValueError(f"invalid operation_ids {invalid}. Valid: {sorted(valid_op_ids)}")
            selected_ids = selection.ordered_operation_ids
            log.info("Phase1 OK (attempt %d): %s", attempt, selected_ids)
            break
        except Exception as e:
            last_error = e
            log.warning("Phase1 attempt %d failed: %s", attempt, e)
    else:
        return {
            "flow_card": {},
            "validation_errors": [f"scenario_analyst phase1 failed: {last_error}"],
            "trace": ["scenario_analyst"],
        }

    # ── Фаза 2: биндинг параметров — по одному шагу ─────────────────────────
    env_var_names = ", ".join(sorted(CONFIG.env_vars.keys())) if CONFIG.env_vars else "(none)"
    SYSTEM_BIND = SYSTEM_BIND_TEMPLATE.format(env_var_names=env_var_names)
    bind_chain = (
        ChatPromptTemplate.from_messages([("system", SYSTEM_BIND), ("human", HUMAN_BIND)])
        | llm.with_structured_output(StepInputs)
    )

    steps: list[ScenarioStep] = []
    for i, op_id in enumerate(selected_ids):
        step_id = f"step_{i + 1:02d}"
        ep = ep_map[op_id]
        depends_on = [s.step_id for s in steps]

        step_inputs: StepInputs | None = None
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                step_inputs = bind_chain.invoke({
                    "step_id": step_id,
                    "operation_id": op_id,
                    "endpoint_spec": _endpoint_spec_text(ep),
                    "previous_context": _previous_context_text(steps, ep_map),
                })
                # Принцип 3.5: Python форсит инварианты после LLM

                # Авто-ремонт Authorization (Принцип 3.5 — инварианты в коде).
                # Случаи когда модель ошибается с токеном:
                #   1. FROM_STEP без source_ref/source_field (модель забыла ссылку на шаг логина)
                #   2. ENV с несуществующим ключом (модель указала устаревший/удалённый env var)
                # В обоих случаях: берём токен из ближайшего предыдущего шага с $.token в produces.
                token_step = next(
                    (s for s in reversed(steps) if any("token" in p.lower() for p in s.produces)),
                    None,
                )
                repaired = []
                for inp in step_inputs.inputs:
                    is_auth = (inp.target_location or "").lower() == "header.authorization"
                    needs_repair = False
                    if is_auth:
                        if inp.source == VarSource.FROM_STEP and (not inp.source_ref or not inp.source_field):
                            needs_repair = True
                        elif inp.source == VarSource.ENV and inp.value and inp.value not in CONFIG.env_vars:
                            needs_repair = True
                    if needs_repair and token_step is not None:
                        token_field = next(
                            (p for p in token_step.produces if "token" in p.lower()), "$.token"
                        )
                        inp = inp.model_copy(update={
                            "source": VarSource.FROM_STEP,
                            "source_ref": token_step.step_id,
                            "source_field": token_field,
                            "value": None,
                        })
                        log.info("Auto-repaired Authorization → from_step %s %s",
                                 token_step.step_id, token_field)
                    repaired.append(inp)
                step_inputs = StepInputs(inputs=repaired, produces=step_inputs.produces)

                no_location = [inp.name for inp in step_inputs.inputs if not inp.target_location]
                if no_location:
                    raise ValueError(f"{step_id}: missing target_location: {no_location}")
                broken_ref = [
                    inp.name for inp in step_inputs.inputs
                    if inp.source.value == "from_step"
                    and (not inp.source_ref or not inp.source_field)
                ]
                if broken_ref:
                    raise ValueError(
                        f"{step_id}: from_step inputs missing source_ref/source_field: {broken_ref}"
                    )
                log.info("Phase2 %s OK (attempt %d): %s",
                         step_id, attempt, [inp.name for inp in step_inputs.inputs])
                for inp in step_inputs.inputs:
                    log.debug("  %s: source=%s loc=%s val=%s",
                              inp.name, inp.source.value, inp.target_location,
                              inp.value or inp.generator or inp.source_field)
                break
            except Exception as e:
                last_error = e
                log.warning("Phase2 %s attempt %d failed: %s", step_id, attempt, e)
        else:
            return {
                "flow_card": {},
                "validation_errors": [f"scenario_analyst phase2 {step_id} failed: {last_error}"],
                "trace": ["scenario_analyst"],
            }

        steps.append(ScenarioStep(
            step_id=step_id,
            operation_id=op_id,
            inputs=step_inputs.inputs,
            produces=step_inputs.produces,
            depends_on=depends_on,
        ))

    flow_card = FlowCard(
        flow_id="generated_flow",
        name="Generated Flow",
        description=raw_scenarios[:200],
        steps=steps,
    )

    log.info("FlowCard built: %d steps -> %s", len(steps), [s.operation_id for s in steps])
    return {
        "flow_card": flow_card.model_dump(),
        "trace": ["scenario_analyst"],
    }
