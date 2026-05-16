"""
Generator Function Registry.

Every variable that Agent 3 needs to produce at runtime has a corresponding
:class:`GeneratorFunction` entry that defines:

* ``python_expr`` — a single-line Python expression evaluated inside Agent 2
  to produce the actual value.
* ``js_snippet``  — a JavaScript block that calls
  ``pm.collectionVariables.set(name, value)`` — pasted verbatim into
  Postman pre-request scripts.

Built-in functions are defined in code and are always available.
When the LLM fallback in Agent 3 creates a generator for a previously unknown
variable it serialises the new :class:`GeneratorFunction` to
``data/generator_registry.json`` so subsequent runs reuse it without another
LLM call.

Loading order (later entries override earlier ones):
  1. Built-in functions (hardcoded in this module).
  2. LLM-generated functions from ``data/generator_registry.json``.
"""

from __future__ import annotations

import json
import logging
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class GeneratorFunction(BaseModel):
    """One data-generation function, expressed in both Python and JavaScript."""

    name: str = Field(description="Exact {{varName}} this function handles.")
    description: str = Field(description="Human-readable description of what is generated.")
    python_expr: str = Field(
        description=(
            "Single-line Python expression (no imports, no def). "
            "Evaluated inside _PYTHON_EVAL_CONTEXT which provides: "
            "datetime, timedelta, timezone, random, string, uuid, context."
        )
    )
    js_snippet: str = Field(
        description=(
            "JavaScript block that ends with pm.collectionVariables.set(name, value). "
            "Pasted into Postman pre-request scripts."
        )
    )
    source: Literal["builtin", "llm_generated"] = Field(
        default="builtin",
        description="'builtin' = defined in code; 'llm_generated' = created by LLM fallback.",
    )
    created_at: Optional[str] = Field(
        default=None,
        description="ISO-8601 timestamp of when this function was created (LLM-generated only).",
    )


# ---------------------------------------------------------------------------
# Python evaluation context
# ---------------------------------------------------------------------------

_PYTHON_EVAL_CONTEXT: Dict[str, Any] = {
    # datetime
    "datetime": datetime,
    "timedelta": timedelta,
    "timezone": timezone,
    # stdlib
    "random": random,
    "string": string,
    "uuid": uuid,
    # builtins subset
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
    "len": len,
    "list": list,
    "dict": dict,
    "sorted": sorted,
    "range": range,
}

# ---------------------------------------------------------------------------
# Built-in function definitions
# ---------------------------------------------------------------------------

_BUILTIN_FUNCTIONS: List[GeneratorFunction] = [

    # ── Datetime ──────────────────────────────────────────────────────────

    GeneratorFunction(
        name="startDate",
        description="ISO-8601 UTC datetime 24 h in the future",
        python_expr=(
            "(datetime.now(tz=timezone.utc) + timedelta(hours=24))"
            ".strftime('%Y-%m-%dT%H:%M:%SZ')"
        ),
        js_snippet=(
            "// Auto-generate startDate: ISO-8601, 24 h in the future\n"
            "const _sd = new Date(Date.now() + 86400000);\n"
            "pm.collectionVariables.set('startDate', _sd.toISOString().replace(/\\.\\d{3}Z$/, 'Z'));"
        ),
    ),
    GeneratorFunction(
        name="endDate",
        description="ISO-8601 UTC datetime 48 h in the future",
        python_expr=(
            "(datetime.now(tz=timezone.utc) + timedelta(hours=48))"
            ".strftime('%Y-%m-%dT%H:%M:%SZ')"
        ),
        js_snippet=(
            "// Auto-generate endDate: ISO-8601, 48 h in the future\n"
            "const _ed = new Date(Date.now() + 172800000);\n"
            "pm.collectionVariables.set('endDate', _ed.toISOString().replace(/\\.\\d{3}Z$/, 'Z'));"
        ),
    ),
    GeneratorFunction(
        name="pickupDate",
        description="ISO-8601 UTC datetime 24 h in the future (alias for startDate)",
        python_expr=(
            "(datetime.now(tz=timezone.utc) + timedelta(hours=24))"
            ".strftime('%Y-%m-%dT%H:%M:%SZ')"
        ),
        js_snippet=(
            "// Auto-generate pickupDate: ISO-8601, 24 h in the future\n"
            "const _pd = new Date(Date.now() + 86400000);\n"
            "pm.collectionVariables.set('pickupDate', _pd.toISOString().replace(/\\.\\d{3}Z$/, 'Z'));"
        ),
    ),
    GeneratorFunction(
        name="returnDate",
        description="ISO-8601 UTC datetime 48 h in the future (alias for endDate)",
        python_expr=(
            "(datetime.now(tz=timezone.utc) + timedelta(hours=48))"
            ".strftime('%Y-%m-%dT%H:%M:%SZ')"
        ),
        js_snippet=(
            "// Auto-generate returnDate: ISO-8601, 48 h in the future\n"
            "const _rd = new Date(Date.now() + 172800000);\n"
            "pm.collectionVariables.set('returnDate', _rd.toISOString().replace(/\\.\\d{3}Z$/, 'Z'));"
        ),
    ),
    GeneratorFunction(
        name="currentDate",
        description="Current UTC date in YYYY-MM-DD format",
        python_expr="datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')",
        js_snippet=(
            "// Auto-generate currentDate: today's date\n"
            "pm.collectionVariables.set('currentDate', new Date().toISOString().slice(0, 10));"
        ),
    ),
    GeneratorFunction(
        name="currentTimestamp",
        description="Current Unix timestamp in milliseconds",
        python_expr="str(int(datetime.now(tz=timezone.utc).timestamp() * 1000))",
        js_snippet=(
            "// Auto-generate currentTimestamp\n"
            "pm.collectionVariables.set('currentTimestamp', String(Date.now()));"
        ),
    ),

    # ── UUIDs / IDs ───────────────────────────────────────────────────────

    GeneratorFunction(
        name="requestId",
        description="Random UUID v4 for correlation/trace IDs",
        python_expr="str(uuid.uuid4())",
        js_snippet=(
            "// Auto-generate requestId: UUID v4\n"
            "pm.collectionVariables.set('requestId', pm.variables.replaceIn('{{$guid}}'));"
        ),
    ),
    GeneratorFunction(
        name="correlationId",
        description="Random UUID v4",
        python_expr="str(uuid.uuid4())",
        js_snippet=(
            "// Auto-generate correlationId: UUID v4\n"
            "pm.collectionVariables.set('correlationId', pm.variables.replaceIn('{{$guid}}'));"
        ),
    ),
    GeneratorFunction(
        name="sessionId",
        description="Random UUID v4 session identifier",
        python_expr="str(uuid.uuid4())",
        js_snippet=(
            "// Auto-generate sessionId: UUID v4\n"
            "pm.collectionVariables.set('sessionId', pm.variables.replaceIn('{{$guid}}'));"
        ),
    ),
    GeneratorFunction(
        name="traceId",
        description="Random 32-char hex trace ID",
        python_expr="uuid.uuid4().hex",
        js_snippet=(
            "// Auto-generate traceId: 32-char hex\n"
            "pm.collectionVariables.set('traceId', pm.variables.replaceIn('{{$guid}}').replace(/-/g, ''));"
        ),
    ),
    GeneratorFunction(
        name="token",
        description="Random 32-character hex token",
        python_expr="uuid.uuid4().hex",
        js_snippet=(
            "// Auto-generate token: 32-char hex\n"
            "pm.collectionVariables.set('token', pm.variables.replaceIn('{{$guid}}').replace(/-/g, ''));"
        ),
    ),
    GeneratorFunction(
        name="apiKey",
        description="Random 40-character hex API key",
        python_expr="uuid.uuid4().hex + uuid.uuid4().hex[:8]",
        js_snippet=(
            "// Auto-generate apiKey: 40-char hex\n"
            "const _ak = (pm.variables.replaceIn('{{$guid}}') + pm.variables.replaceIn('{{$guid}}')).replace(/-/g, '').slice(0, 40);\n"
            "pm.collectionVariables.set('apiKey', _ak);"
        ),
    ),

    # ── Personal data ─────────────────────────────────────────────────────

    GeneratorFunction(
        name="email",
        description="Generated test email address",
        python_expr="'testuser_' + uuid.uuid4().hex[:8] + '@example.com'",
        js_snippet=(
            "// Auto-generate email\n"
            "pm.collectionVariables.set('email', 'testuser_' + pm.variables.replaceIn('{{$randomInt}}') + '@example.com');"
        ),
    ),
    GeneratorFunction(
        name="userEmail",
        description="Generated test email address (alias for email)",
        python_expr="'testuser_' + uuid.uuid4().hex[:8] + '@example.com'",
        js_snippet=(
            "// Auto-generate userEmail\n"
            "pm.collectionVariables.set('userEmail', 'testuser_' + pm.variables.replaceIn('{{$randomInt}}') + '@example.com');"
        ),
    ),
    GeneratorFunction(
        name="firstName",
        description="Random first name for test data",
        python_expr="random.choice(['Alice','Bob','Carol','Dave','Eve','Frank','Grace','Hank'])",
        js_snippet=(
            "// Auto-generate firstName\n"
            "const _fn = ['Alice','Bob','Carol','Dave','Eve','Frank','Grace','Hank'];\n"
            "pm.collectionVariables.set('firstName', _fn[Math.floor(Math.random() * _fn.length)]);"
        ),
    ),
    GeneratorFunction(
        name="lastName",
        description="Random last name for test data",
        python_expr="random.choice(['Smith','Jones','Brown','Taylor','Wilson','Moore','Davis'])",
        js_snippet=(
            "// Auto-generate lastName\n"
            "const _ln = ['Smith','Jones','Brown','Taylor','Wilson','Moore','Davis'];\n"
            "pm.collectionVariables.set('lastName', _ln[Math.floor(Math.random() * _ln.length)]);"
        ),
    ),
    GeneratorFunction(
        name="fullName",
        description="Random full name for test data",
        python_expr="random.choice(['Alice Smith','Bob Jones','Carol Brown','Dave Taylor','Eve Wilson'])",
        js_snippet=(
            "// Auto-generate fullName\n"
            "const _fnl = ['Alice Smith','Bob Jones','Carol Brown','Dave Taylor','Eve Wilson'];\n"
            "pm.collectionVariables.set('fullName', _fnl[Math.floor(Math.random() * _fnl.length)]);"
        ),
    ),
    GeneratorFunction(
        name="phone",
        description="Random phone number (+7XXXXXXXXXX)",
        python_expr="'+7' + ''.join(random.choices(string.digits, k=10))",
        js_snippet=(
            "// Auto-generate phone\n"
            "const _ph = '+7' + Array.from({length: 10}, () => Math.floor(Math.random() * 10)).join('');\n"
            "pm.collectionVariables.set('phone', _ph);"
        ),
    ),
    GeneratorFunction(
        name="phoneNumber",
        description="Random phone number (+7XXXXXXXXXX, alias for phone)",
        python_expr="'+7' + ''.join(random.choices(string.digits, k=10))",
        js_snippet=(
            "// Auto-generate phoneNumber\n"
            "const _phn = '+7' + Array.from({length: 10}, () => Math.floor(Math.random() * 10)).join('');\n"
            "pm.collectionVariables.set('phoneNumber', _phn);"
        ),
    ),
    GeneratorFunction(
        name="password",
        description="Random 12-character test password",
        python_expr="'Test@' + ''.join(random.choices(string.ascii_letters + string.digits, k=7))",
        js_snippet=(
            "// Auto-generate password\n"
            "const _pwc = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';\n"
            "const _pw = 'Test@' + Array.from({length: 7}, () => _pwc[Math.floor(Math.random() * _pwc.length)]).join('');\n"
            "pm.collectionVariables.set('password', _pw);"
        ),
    ),

    # ── Addresses ─────────────────────────────────────────────────────────

    GeneratorFunction(
        name="address",
        description="Random street address",
        python_expr="'ul. Lenina ' + str(random.randint(1, 200)) + ', kv. ' + str(random.randint(1, 100))",
        js_snippet=(
            "// Auto-generate address\n"
            "const _addr = 'ul. Lenina ' + (Math.floor(Math.random()*200)+1) + ', kv. ' + (Math.floor(Math.random()*100)+1);\n"
            "pm.collectionVariables.set('address', _addr);"
        ),
    ),
    GeneratorFunction(
        name="streetAddress",
        description="Random street address (short form)",
        python_expr="'ul. Mira ' + str(random.randint(1, 200))",
        js_snippet=(
            "// Auto-generate streetAddress\n"
            "pm.collectionVariables.set('streetAddress', 'ul. Mira ' + (Math.floor(Math.random()*200)+1));"
        ),
    ),

    # ── Vehicles ──────────────────────────────────────────────────────────

    GeneratorFunction(
        name="licensePlate",
        description="Random Russian-style vehicle license plate",
        python_expr=(
            "random.choice('АВЕКМНОРСТУХ') + str(random.randint(100,999))"
            " + random.choice('АВЕКМНОРСТУХ') + random.choice('АВЕКМНОРСТУХ')"
            " + str(random.randint(10,199))"
        ),
        js_snippet=(
            "// Auto-generate licensePlate (Russian format)\n"
            "const _lc = 'АВЕКМНОРСТУХ';\n"
            "const _lp = _lc[Math.floor(Math.random()*_lc.length)]\n"
            "    + (Math.floor(Math.random()*900)+100)\n"
            "    + _lc[Math.floor(Math.random()*_lc.length)]\n"
            "    + _lc[Math.floor(Math.random()*_lc.length)]\n"
            "    + (Math.floor(Math.random()*190)+10);\n"
            "pm.collectionVariables.set('licensePlate', _lp);"
        ),
    ),
    GeneratorFunction(
        name="plateNumber",
        description="Random Russian-style vehicle license plate (alias)",
        python_expr=(
            "random.choice('АВЕКМНОРСТУХ') + str(random.randint(100,999))"
            " + random.choice('АВЕКМНОРСТУХ') + random.choice('АВЕКМНОРСТУХ')"
            " + str(random.randint(10,199))"
        ),
        js_snippet=(
            "// Auto-generate plateNumber (Russian format)\n"
            "const _lc2 = 'АВЕКМНОРСТУХ';\n"
            "const _pln = _lc2[Math.floor(Math.random()*_lc2.length)]\n"
            "    + (Math.floor(Math.random()*900)+100)\n"
            "    + _lc2[Math.floor(Math.random()*_lc2.length)]\n"
            "    + _lc2[Math.floor(Math.random()*_lc2.length)]\n"
            "    + (Math.floor(Math.random()*190)+10);\n"
            "pm.collectionVariables.set('plateNumber', _pln);"
        ),
    ),

    # ── Numeric ───────────────────────────────────────────────────────────

    GeneratorFunction(
        name="amount",
        description="Random monetary amount (100.00 – 5000.00)",
        python_expr="round(random.uniform(100, 5000), 2)",
        js_snippet=(
            "// Auto-generate amount\n"
            "pm.collectionVariables.set('amount', String((Math.random() * 4900 + 100).toFixed(2)));"
        ),
    ),
    GeneratorFunction(
        name="price",
        description="Random price (100.00 – 5000.00)",
        python_expr="round(random.uniform(100, 5000), 2)",
        js_snippet=(
            "// Auto-generate price\n"
            "pm.collectionVariables.set('price', String((Math.random() * 4900 + 100).toFixed(2)));"
        ),
    ),
    GeneratorFunction(
        name="quantity",
        description="Random integer quantity (1 – 100)",
        python_expr="random.randint(1, 100)",
        js_snippet=(
            "// Auto-generate quantity\n"
            "pm.collectionVariables.set('quantity', String(Math.floor(Math.random()*100)+1));"
        ),
    ),
    GeneratorFunction(
        name="count",
        description="Random integer count (1 – 50)",
        python_expr="random.randint(1, 50)",
        js_snippet=(
            "// Auto-generate count\n"
            "pm.collectionVariables.set('count', String(Math.floor(Math.random()*50)+1));"
        ),
    ),
]

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY_PATH = Path("data/generator_registry.json")


class GeneratorRegistry:
    """
    Registry of all known generator functions.

    Built-in functions are always loaded from code.  LLM-generated functions
    are persisted to ``registry_path`` (default: ``data/generator_registry.json``)
    and loaded on startup, so the registry grows automatically as Agent 3
    encounters new variable names.

    LLM-generated entries *override* built-in entries with the same name,
    which lets engineers patch a built-in by running Agent 3 once and saving
    the improved version.
    """

    def __init__(self, registry_path: Path = _DEFAULT_REGISTRY_PATH) -> None:
        self._path = registry_path
        # Load built-ins first
        self._functions: Dict[str, GeneratorFunction] = {
            f.name: f for f in _BUILTIN_FUNCTIONS
        }
        self._load_from_file()
        logger.debug(
            "GeneratorRegistry: loaded %d functions (%d builtin, %d from file)",
            len(self._functions),
            sum(1 for f in self._functions.values() if f.source == "builtin"),
            sum(1 for f in self._functions.values() if f.source == "llm_generated"),
        )

    # ------------------------------------------------------------------
    # Lookup / execute
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> Optional[GeneratorFunction]:
        """Return the generator function for *name*, or ``None`` if not found."""
        return self._functions.get(name)

    def execute(self, func: GeneratorFunction, context: Dict[str, Any]) -> Any:
        """
        Evaluate *func.python_expr* and return the result.

        The expression is evaluated inside :data:`_PYTHON_EVAL_CONTEXT` extended
        with ``context`` (the current variable context from Agent 2).
        """
        eval_ctx = {**_PYTHON_EVAL_CONTEXT, "context": context}
        try:
            # We allow full builtins since expressions come from our own registry
            # (builtin functions defined in code, or LLM-generated and persisted for review).
            return eval(func.python_expr, eval_ctx)  # noqa: S307
        except Exception as exc:
            logger.error(
                "GeneratorRegistry: failed to execute '%s' (%r): %s",
                func.name, func.python_expr, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Registration (LLM-generated functions)
    # ------------------------------------------------------------------

    def register(self, func: GeneratorFunction) -> None:
        """
        Add a new function (typically LLM-generated) and persist it to disk.

        If a function with the same name already exists it is overwritten.
        """
        func = func.model_copy(update={"source": "llm_generated"})
        self._functions[func.name] = func
        self._save()
        logger.info(
            "GeneratorRegistry: saved new function '%s' → %s",
            func.name, self._path,
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def all_functions(self) -> List[GeneratorFunction]:
        """Return all registered functions (builtin + LLM-generated), sorted by name."""
        return sorted(self._functions.values(), key=lambda f: f.name)

    def llm_generated(self) -> List[GeneratorFunction]:
        """Return only LLM-generated functions."""
        return [f for f in self._functions.values() if f.source == "llm_generated"]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_from_file(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            loaded = 0
            for entry in data.get("functions", []):
                func = GeneratorFunction.model_validate(entry)
                self._functions[func.name] = func
                loaded += 1
            if loaded:
                logger.info(
                    "GeneratorRegistry: loaded %d LLM-generated function(s) from %s",
                    loaded, self._path,
                )
        except Exception as exc:
            logger.warning(
                "GeneratorRegistry: could not load %s: %s", self._path, exc
            )

    def _save(self) -> None:
        """Persist only LLM-generated functions to the registry file."""
        llm_funcs = [
            f.model_dump()
            for f in sorted(self._functions.values(), key=lambda f: f.name)
            if f.source == "llm_generated"
        ]
        payload = {
            "version": 1,
            "_note": (
                "Auto-generated by Agent 3 (DataGeneratorAgent). "
                "Built-in functions live in src/agents/generator_registry.py. "
                "Edit this file to override or supplement built-ins."
            ),
            "functions": llm_funcs,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
