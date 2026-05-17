"""Executable JavaScript data generation policies used by Agent 3."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel


GenerationStrategy = Literal["builtin", "custom"]


class GeneratorPolicy(BaseModel):
    name: str
    strategy: GenerationStrategy
    description: str
    generator_function: str
    js_code: str
    js_snippet: str

    @property
    def python_expr(self) -> str:
        """Backward-compatible report field; this code is JavaScript."""

        return self.generator_function


POLICIES: Dict[str, GeneratorPolicy] = {
    "startDate": GeneratorPolicy(
        name="startDate",
        strategy="builtin",
        description="ISO-8601 UTC datetime one day in the future.",
        generator_function="function generate_startDate(context, feedback) { return new Date(Date.now() + 24*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z'); }",
        js_code="const value = new Date(Date.now() + 24*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z');",
        js_snippet="const startDate = new Date(Date.now() + 24*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z');\npm.collectionVariables.set('startDate', startDate);",
    ),
    "endDate": GeneratorPolicy(
        name="endDate",
        strategy="builtin",
        description="ISO-8601 UTC datetime two days in the future.",
        generator_function="function generate_endDate(context, feedback) { return new Date(Date.now() + 48*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z'); }",
        js_code="const value = new Date(Date.now() + 48*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z');",
        js_snippet="const endDate = new Date(Date.now() + 48*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z');\npm.collectionVariables.set('endDate', endDate);",
    ),
    "requestId": GeneratorPolicy(
        name="requestId",
        strategy="builtin",
        description="Random UUID request id.",
        generator_function="function generate_requestId(context, feedback) { return crypto.randomUUID(); }",
        js_code="const value = crypto.randomUUID();",
        js_snippet="pm.collectionVariables.set('requestId', pm.variables.replaceIn('{{$guid}}'));",
    ),
    "phone": GeneratorPolicy(
        name="phone",
        strategy="builtin",
        description="Synthetic phone number.",
        generator_function="function generate_phone(context, feedback) { return '+79' + Math.floor(100000000 + Math.random()*900000000); }",
        js_code="const value = '+79' + Math.floor(100000000 + Math.random()*900000000);",
        js_snippet="pm.collectionVariables.set('phone', '+79' + Math.floor(100000000 + Math.random()*900000000));",
    ),
    "email": GeneratorPolicy(
        name="email",
        strategy="builtin",
        description="Synthetic unique email address.",
        generator_function="function generate_email(context, feedback) { return 'user_' + crypto.randomUUID().replace(/-/g, '').slice(0, 10) + '@example.test'; }",
        js_code="const value = 'user_' + crypto.randomUUID().replace(/-/g, '').slice(0, 10) + '@example.test';",
        js_snippet="pm.collectionVariables.set('email', 'user_' + pm.variables.replaceIn('{{$guid}}').replace(/-/g, '').slice(0, 10) + '@example.test');",
    ),
    "amount": GeneratorPolicy(
        name="amount",
        strategy="builtin",
        description="Positive decimal amount.",
        generator_function="function generate_amount(context, feedback) { return Number((10 + Math.random()*990).toFixed(2)); }",
        js_code="const value = Number((10 + Math.random()*990).toFixed(2));",
        js_snippet="pm.collectionVariables.set('amount', Number((10 + Math.random()*990).toFixed(2)));",
    ),
}


def policy_for(variable_name: str, previous_error: Optional[str] = None) -> GeneratorPolicy:
    if variable_name in POLICIES:
        return POLICIES[variable_name]

    lowered = variable_name.lower()
    if "date" in lowered:
        return _named_builtin(
            variable_name,
            "ISO-8601 UTC datetime one day in the future.",
            "return new Date(Date.now() + 24*60*60*1000).toISOString().replace(/\\.\\d{3}Z$/, 'Z');",
        )
    if "email" in lowered:
        return _named_builtin(
            variable_name,
            "Synthetic unique email address.",
            "return 'user_' + crypto.randomUUID().replace(/-/g, '').slice(0, 10) + '@example.test';",
        )
    if "phone" in lowered:
        return _named_builtin(
            variable_name,
            "Synthetic phone number.",
            "return '+79' + Math.floor(100000000 + Math.random()*900000000);",
        )
    if any(token in lowered for token in ("amount", "price", "sum", "total", "cost")):
        return _named_builtin(
            variable_name,
            "Positive decimal amount.",
            "return Number((10 + Math.random()*990).toFixed(2));",
        )
    if lowered.endswith("id"):
        return _named_builtin(
            variable_name,
            "Synthetic stable identifier for standalone or negative test data.",
            f"return '{variable_name}-' + crypto.randomUUID().replace(/-/g, '').slice(0, 12);",
        )

    return custom_policy_for(variable_name, previous_error)


def custom_policy_for(variable_name: str, previous_error: Optional[str] = None) -> GeneratorPolicy:
    feedback_hint = f" Feedback from Agent 2: {previous_error}" if previous_error else ""
    function_body = f"return '{variable_name}_' + crypto.randomUUID().replace(/-/g, '').slice(0, 8);"
    return GeneratorPolicy(
        name=variable_name,
        strategy="custom",
        description=f"Custom JS generator created because no built-in policy matched.{feedback_hint}",
        generator_function=f"function generate_{variable_name}(context, feedback) {{ {function_body} }}",
        js_code=f"const value = (() => {{ {function_body} }})();",
        js_snippet=f"pm.collectionVariables.set('{variable_name}', '{variable_name}_' + pm.variables.replaceIn('{{{{$guid}}}}').replace(/-/g, '').slice(0, 8));",
    )


def execute_policy(policy: GeneratorPolicy, context: Dict[str, Any], previous_error: Optional[str] = None) -> Any:
    script = _node_harness(policy)
    payload = json.dumps({"context": context, "feedback": previous_error}, ensure_ascii=False)
    completed = subprocess.run(
        ["node", "-e", script, payload],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    result = json.loads(completed.stdout)
    return result["value"]


def _named_builtin(variable_name: str, description: str, return_statement: str) -> GeneratorPolicy:
    js_code = f"const value = (() => {{ {return_statement} }})();"
    return GeneratorPolicy(
        name=variable_name,
        strategy="builtin",
        description=description,
        generator_function=f"function generate_{variable_name}(context, feedback) {{ {return_statement} }}",
        js_code=js_code,
        js_snippet=f"{js_code}\npm.collectionVariables.set('{variable_name}', value);",
    )


def _node_harness(policy: GeneratorPolicy) -> str:
    return f"""
const crypto = require('crypto');
const input = JSON.parse(process.argv[1] || '{{}}');
const context = input.context || {{}};
const feedback = input.feedback || null;
const collectionVariables = new Map(Object.entries(context));
const pm = {{
  collectionVariables: {{
    set: (key, value) => collectionVariables.set(key, value),
    get: (key) => collectionVariables.get(key)
  }},
  variables: {{
    replaceIn: (value) => String(value).replace(/\\{{\\{{$guid\\}}\\}}/g, () => crypto.randomUUID())
  }}
}};
{policy.js_code}
const generated = typeof value !== 'undefined' ? value : pm.collectionVariables.get({json.dumps(policy.name)});
process.stdout.write(JSON.stringify({{value: generated}}));
"""
