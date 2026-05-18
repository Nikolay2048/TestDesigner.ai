"""Parser for system-analysis scenario markdown."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from src.testdesigner.models import ParsedScenario, ParsedScenarioStep


class ScenarioParser:
    ENDPOINT_RE = re.compile(r"(?im)^\s*Endpoint:\s+(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`]+)\s*$")
    STATUS_RE = re.compile(r"\b([245]\d\d)\b")
    FENCE_RE = re.compile(r"```(?:json|yaml|yml)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

    def parse(self, text: str) -> ParsedScenario:
        matches = list(self.ENDPOINT_RE.finditer(text))
        steps: list[ParsedScenarioStep] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            section = text[match.start():end].strip()
            raw_path = match.group(2).strip()
            path, query = self._split_query(raw_path)
            steps.append(ParsedScenarioStep(
                index=index + 1,
                method=match.group(1).upper(),  # type: ignore[arg-type]
                path=path,
                text=section[:2500],
                expected_status=self._status(section),
                request_body=self._body(section),
                query_params=query,
            ))
        return ParsedScenario(
            title=self._title(text),
            raw_text=text,
            steps=steps,
            business_rules=self._rules(text),
        )

    def _status(self, section: str) -> int | None:
        values = [int(v) for v in self.STATUS_RE.findall(section)]
        return values[0] if values else None

    def _body(self, section: str) -> Any:
        for fenced in self.FENCE_RE.findall(section):
            parsed = self._parse_structured(fenced)
            if isinstance(parsed, (dict, list)):
                return parsed
        marker = re.search(r"(?i)(?:request body|тело запроса)\s*:\s*", section)
        if marker:
            tail = section[marker.end():]
            block = self._first_json_object(tail)
            if block:
                return self._parse_structured(block)
        return None

    @staticmethod
    def _first_json_object(text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        for index, char in enumerate(text[start:], start=start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return None

    @staticmethod
    def _parse_structured(raw: str) -> Any:
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return yaml.safe_load(raw)
            except yaml.YAMLError:
                return None

    @staticmethod
    def _split_query(path: str) -> tuple[str, dict[str, Any]]:
        if "?" not in path:
            return path, {}
        clean, raw_query = path.split("?", 1)
        query: dict[str, Any] = {}
        for item in raw_query.split("&"):
            key, _, value = item.partition("=")
            if key:
                query[key] = value
        return clean, query

    @staticmethod
    def _title(text: str) -> str:
        return next((line.strip("# \t") for line in text.splitlines() if line.strip()), "Generated REST scenario")[:160]

    @staticmethod
    def _rules(text: str) -> list[str]:
        result: list[str] = []
        for line in text.splitlines():
            cleaned = line.strip(" -*\t")
            low = cleaned.lower()
            if any(token in low for token in ("долж", "нельзя", "must", "should", "required", "invalid", "duplicate")):
                result.append(cleaned[:300])
        return result[:40]
