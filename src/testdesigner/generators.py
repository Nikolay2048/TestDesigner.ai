"""Data generation policies used by Agent 3."""

from __future__ import annotations

import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from pydantic import BaseModel


class GeneratorPolicy(BaseModel):
    name: str
    description: str
    python_expr: str
    js_snippet: str


POLICIES: Dict[str, GeneratorPolicy] = {
    "startDate": GeneratorPolicy(
        name="startDate",
        description="ISO-8601 UTC datetime one day in the future.",
        python_expr="(datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')",
        js_snippet="const startDate = new Date(Date.now() + 24*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z');\npm.collectionVariables.set('startDate', startDate);",
    ),
    "endDate": GeneratorPolicy(
        name="endDate",
        description="ISO-8601 UTC datetime two days in the future.",
        python_expr="(datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')",
        js_snippet="const endDate = new Date(Date.now() + 48*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z');\npm.collectionVariables.set('endDate', endDate);",
    ),
    "requestId": GeneratorPolicy(
        name="requestId",
        description="Random UUID request id.",
        python_expr="str(uuid.uuid4())",
        js_snippet="pm.collectionVariables.set('requestId', pm.variables.replaceIn('{{$guid}}'));",
    ),
    "phone": GeneratorPolicy(
        name="phone",
        description="Russian-style synthetic phone number.",
        python_expr="'+79' + ''.join(random.choice(string.digits) for _ in range(9))",
        js_snippet="pm.collectionVariables.set('phone', '+79' + Math.floor(100000000 + Math.random()*900000000));",
    ),
}


def policy_for(variable_name: str) -> GeneratorPolicy:
    if variable_name in POLICIES:
        return POLICIES[variable_name]
    lowered = variable_name.lower()
    if "date" in lowered:
        return POLICIES["startDate"].model_copy(update={"name": variable_name})
    if lowered.endswith("id"):
        return GeneratorPolicy(
            name=variable_name,
            description="Synthetic id for negative or standalone test data.",
            python_expr="'" + variable_name + "-' + uuid.uuid4().hex[:8]",
            js_snippet=f"pm.collectionVariables.set('{variable_name}', '{variable_name}-' + pm.variables.replaceIn('{{$guid}}').slice(0, 8));",
        )
    return GeneratorPolicy(
        name=variable_name,
        description="Synthetic string value.",
        python_expr="'" + variable_name + "_' + uuid.uuid4().hex[:8]",
        js_snippet=f"pm.collectionVariables.set('{variable_name}', '{variable_name}_' + pm.variables.replaceIn('{{$guid}}').slice(0, 8));",
    )


def execute_policy(policy: GeneratorPolicy, context: Dict[str, Any]) -> Any:
    eval_context = {
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": timezone,
        "random": random,
        "string": string,
        "uuid": uuid,
        "context": context,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "round": round,
    }
    return eval(policy.python_expr, eval_context)  # noqa: S307
