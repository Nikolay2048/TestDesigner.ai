from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool

from old.domain import GeneratorSpec


PythonGenerator = Callable[..., Any]


def uuid_generator() -> str:
    """Return a random UUID string."""


def email(domain: str = "test.com") -> str:
    """Return a unique test email address for the provided domain."""


def phone_number(country: str = "RU", format: str = "e164") -> str:
    """Return a phone number string for a country and output format."""


def full_name() -> str:
    """Return a simple human full name."""


def random_string(prefix: str = "test", length: int = 16) -> str:
    """Return a synthetic generic string with a stable prefix."""


def driver_license_number(country: str = "RU") -> str:
    """Return a synthetic driver license number for test data."""


def payment_card_token(provider: str = "mock") -> str:
    """Return a valid payment card token for a mock payment provider."""


def date_after_now(days: int = 1, format: str = "iso_datetime") -> str:
    """Return a date or datetime after current time. Use format='date' for YYYY-MM-DD."""


def random_int(min: int = 0, max: int = 1000) -> int:
    """Return a random integer between min and max, inclusive."""


def enum_value(values: list[Any]) -> Any:
    """Select one value from a non-empty list of allowed values."""


class GeneratorRegistry:
    """Deterministic generator catalog used by executor and agents."""

    def __init__(self):
        self._python_generators: dict[str, PythonGenerator] = {
            "uuid": self._uuid,
            "email": self._email,
            "phone_number": self._phone_number,
            "full_name": self._full_name,
            "random_string": self._random_string,
            "driver_license_number": self._driver_license_number,
            "payment_card_token": self._payment_card_token,
            "date_after_now": self._date_after_now,
            "random_int": self._random_int,
            "enum_value": self._enum_value,
        }
        self._tool_functions: dict[str, Callable[..., Any]] = {
            "uuid": uuid_generator,
            "email": email,
            "phone_number": phone_number,
            "full_name": full_name,
            "random_string": random_string,
            "driver_license_number": driver_license_number,
            "payment_card_token": payment_card_token,
            "date_after_now": date_after_now,
            "random_int": random_int,
            "enum_value": enum_value,
        }
        self._tool_schemas: dict[str, dict[str, Any]] = {
            name: convert_to_openai_tool(function)
            for name, function in self._tool_functions.items()
        }
        self._tool_schemas["uuid"]["function"]["name"] = "uuid"
        self._specs: dict[str, GeneratorSpec] = {
            "uuid": GeneratorSpec(name="uuid", description="Random UUID string."),
            "email": GeneratorSpec(
                name="email",
                description="Unique test email.",
                parameters={"domain": "Email domain, default test.com."},
            ),
            "phone_number": GeneratorSpec(
                name="phone_number",
                description="Phone number string.",
                parameters={
                    "country": "Country code, for example RU.",
                    "format": "e164 or local, default e164.",
                },
            ),
            "full_name": GeneratorSpec(name="full_name", description="Simple human full name."),
            "random_string": GeneratorSpec(
                name="random_string",
                description="Generic synthetic string for fields without a specialized generator.",
                parameters={
                    "prefix": "Readable prefix, default test.",
                    "length": "Maximum result length, default 16.",
                },
            ),
            "driver_license_number": GeneratorSpec(
                name="driver_license_number",
                description="Synthetic driver license number for test data.",
                parameters={"country": "Country code, for example RU."},
            ),
            "payment_card_token": GeneratorSpec(
                name="payment_card_token",
                description="Valid payment card token for happy-path payment authorization.",
                parameters={"provider": "Payment provider name, default mock."},
            ),
            "date_after_now": GeneratorSpec(
                name="date_after_now",
                description="Date or datetime after current time.",
                parameters={
                    "days": "Days after now, default 1.",
                    "format": "date for YYYY-MM-DD or iso_datetime for full timestamp.",
                },
            ),
            "random_int": GeneratorSpec(
                name="random_int",
                description="Random integer.",
                parameters={"min": "Minimum value.", "max": "Maximum value."},
            ),
            "enum_value": GeneratorSpec(
                name="enum_value",
                description="Select one value from a provided enum list.",
                parameters={"values": "List of allowed values."},
            ),
        }
        self._js_generators: dict[str, str] = {
            "uuid": (
                "function uuid() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, "
                "function(c) { const r = Math.random() * 16 | 0; "
                "const v = c === 'x' ? r : (r & 0x3 | 0x8); return v.toString(16); }); }"
            ),
            "email": (
                "function email(domain = 'test.com') { "
                "return `test_${Date.now()}_${Math.floor(Math.random() * 100000)}@${domain}`; }"
            ),
            "phone_number": (
                "function phoneNumber(country = 'RU', format = 'e164') { "
                "const tail = String(Math.floor(1000000000 + Math.random() * 9000000000)); "
                "if (country === 'RU' && format === 'e164') return '+7' + tail; "
                "return tail; }"
            ),
            "full_name": (
                "function fullName() { "
                "return `Test User ${Math.floor(Math.random() * 100000)}`; }"
            ),
            "random_string": (
                "function randomString(prefix = 'test', length = 16) { "
                "const suffix = `${Date.now()}${Math.floor(Math.random() * 100000)}`; "
                "return `${prefix}_${suffix}`.slice(0, Math.max(1, length)); }"
            ),
            "driver_license_number": (
                "function driverLicenseNumber(country = 'RU') { "
                "if (country === 'RU') return String(Math.floor(1000000000 + Math.random() * 9000000000)); "
                "return `DL-${Date.now()}-${Math.floor(Math.random() * 10000)}`; }"
            ),
            "payment_card_token": (
                "function paymentCardToken(provider = 'mock') { "
                "if (provider === 'mock') return `tok_approved_${Date.now()}_${Math.floor(Math.random() * 10000)}`; "
                "return `tok_${provider}_${Date.now()}`; }"
            ),
            "date_after_now": (
                "function dateAfterNow(days = 1, format = 'iso_datetime') { "
                "const d = new Date(Date.now() + days * 24 * 60 * 60 * 1000); "
                "if (format === 'date') return d.toISOString().slice(0, 10); "
                "return d.toISOString(); }"
            ),
            "random_int": (
                "function randomInt(min = 0, max = 1000) { "
                "return Math.floor(min + Math.random() * (max - min + 1)); }"
            ),
            "enum_value": (
                "function enumValue(values) { "
                "if (!values || !values.length) throw new Error('values is required'); "
                "return values[Math.floor(Math.random() * values.length)]; }"
            ),
        }

    def specs(self) -> list[GeneratorSpec]:
        return list(self._specs.values())

    def tool_schemas(self) -> list[dict[str, Any]]:
        """OpenAI-compatible tool schemas for LLM prompts and future LangGraph tools."""

        return list(self._tool_schemas.values())

    def tool_schema(self, name: str) -> dict[str, Any]:
        if name not in self._tool_schemas:
            raise KeyError(f"Unknown generator tool: {name}")
        return self._tool_schemas[name]

    def has(self, name: str) -> bool:
        return name in self._python_generators and name in self._js_generators

    def generate(self, name: str, params: dict[str, Any] | None = None) -> Any:
        if name not in self._python_generators:
            raise KeyError(f"Unknown generator: {name}")
        coerced_params = self.coerce_params(name, params or {})
        return self._python_generators[name](**coerced_params)

    def coerce_params(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Coerce simple LLM-produced parameter strings according to the tool schema."""

        if name not in self._tool_schemas:
            return params
        properties = (
            self._tool_schemas[name]
            .get("function", {})
            .get("parameters", {})
            .get("properties", {})
        )
        return {
            key: self._coerce_value(value, properties.get(key, {}))
            for key, value in params.items()
        }

    def js_source(self, name: str) -> str:
        if name not in self._js_generators:
            raise KeyError(f"Unknown JS generator: {name}")
        return self._js_generators[name]

    @staticmethod
    def _uuid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _email(domain: str = "test.com") -> str:
        return f"test_{uuid.uuid4().hex[:12]}@{domain}"

    @staticmethod
    def _phone_number(country: str = "RU", format: str = "e164") -> str:
        tail = str(random.randint(1000000000, 9999999999))
        if country.upper() == "RU" and format == "e164":
            return "+7" + tail
        return tail

    @staticmethod
    def _full_name() -> str:
        return f"Test User {random.randint(10000, 99999)}"

    @staticmethod
    def _random_string(prefix: str = "test", length: int = 16) -> str:
        value = f"{prefix}_{uuid.uuid4().hex}"
        return value[: max(1, length)]

    @staticmethod
    def _driver_license_number(country: str = "RU") -> str:
        if country.upper() == "RU":
            return str(random.randint(1000000000, 9999999999))
        return f"DL-{uuid.uuid4().hex[:12].upper()}"

    @staticmethod
    def _payment_card_token(provider: str = "mock") -> str:
        if provider == "mock":
            return f"tok_approved_{uuid.uuid4().hex[:12]}"
        return f"tok_{provider}_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _date_after_now(days: int = 1, format: str = "iso_datetime") -> str:
        value = datetime.now(UTC) + timedelta(days=days)
        if format == "date":
            return value.date().isoformat()
        return value.isoformat()

    @staticmethod
    def _random_int(min: int = 0, max: int = 1000) -> int:
        return random.randint(min, max)

    @staticmethod
    def _enum_value(values: list[Any]) -> Any:
        if not values:
            raise ValueError("values is required")
        return random.choice(values)

    @staticmethod
    def _coerce_value(value: Any, schema: dict[str, Any]) -> Any:
        if not isinstance(value, str):
            return value
        if value.startswith("{{") and value.endswith("}}"):
            return value
        schema_type = schema.get("type")
        if schema_type == "integer" and value.strip().lstrip("-").isdigit():
            return int(value)
        if schema_type == "number":
            try:
                return float(value)
            except ValueError:
                return value
        if schema_type == "boolean" and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        return value
