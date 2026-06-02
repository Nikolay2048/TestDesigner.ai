from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from domain import GeneratorSpec


PythonGenerator = Callable[..., Any]


class GeneratorRegistry:
    """Deterministic generator catalog used by executor and agents."""

    def __init__(self):
        self._python_generators: dict[str, PythonGenerator] = {
            "uuid": self._uuid,
            "email": self._email,
            "phone_number": self._phone_number,
            "full_name": self._full_name,
            "date_after_now": self._date_after_now,
            "random_int": self._random_int,
            "enum_value": self._enum_value,
        }
        self._specs: dict[str, GeneratorSpec] = {
            "uuid": GeneratorSpec(name="uuid", description="Random UUID string."),
            "email": GeneratorSpec(
                name="email",
                description="Unique test email.",
                parameters={"domain": "Email domain, default example.test."},
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
                "function email(domain = 'example.test') { "
                "return `test_${Date.now()}_${Math.floor(Math.random() * 100000)}@${domain}`; }"
            ),
            "phone_number": (
                "function phoneNumber(country = 'RU', format = 'e164') { "
                "const tail = String(Math.floor(1000000000 + Math.random() * 9000000000)); "
                "if (country === 'RU' && format === 'e164') return '+7' + tail.slice(1); "
                "return tail; }"
            ),
            "full_name": (
                "function fullName() { "
                "return `Test User ${Math.floor(Math.random() * 100000)}`; }"
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

    def has(self, name: str) -> bool:
        return name in self._python_generators and name in self._js_generators

    def generate(self, name: str, params: dict[str, Any] | None = None) -> Any:
        if name not in self._python_generators:
            raise KeyError(f"Unknown generator: {name}")
        return self._python_generators[name](**(params or {}))

    def js_source(self, name: str) -> str:
        if name not in self._js_generators:
            raise KeyError(f"Unknown JS generator: {name}")
        return self._js_generators[name]

    @staticmethod
    def _uuid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _email(domain: str = "example.test") -> str:
        return f"test_{uuid.uuid4().hex[:12]}@{domain}"

    @staticmethod
    def _phone_number(country: str = "RU", format: str = "e164") -> str:
        tail = str(random.randint(1000000000, 9999999999))
        if country.upper() == "RU" and format == "e164":
            return "+7" + tail[1:]
        return tail

    @staticmethod
    def _full_name() -> str:
        return f"Test User {random.randint(10000, 99999)}"

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
