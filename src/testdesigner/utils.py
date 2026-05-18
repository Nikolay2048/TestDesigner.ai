"""Shared deterministic utilities."""

from __future__ import annotations

import re
from typing import Any

from jsonpath_ng import parse as parse_jsonpath

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
MOJIBAKE_RE = re.compile(r"[РС][\x80-\xbfЀ-ӿ]")


def repair_mojibake(text: str) -> str:
    """Repair UTF-8 Russian text that was accidentally decoded as cp1251."""
    if not MOJIBAKE_RE.search(text):
        return text
    try:
        repaired = text.encode("cp1251").decode("utf-8")
    except UnicodeError:
        return text
    return repaired if _cyrillic_score(repaired) > _cyrillic_score(text) else text


def _cyrillic_score(text: str) -> int:
    common = "абвгдежзийклмнопрстуфхцчшщъыьэюяёАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЁ"
    noise = "РСЃЃЌЋЏ"
    return sum(1 for char in text if char in common) - sum(2 for char in text if char in noise)


def collect_variables(value: Any) -> set[str]:
    result: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, str):
            result.update(VAR_RE.findall(node))
        elif isinstance(node, dict):
            for item in node.values():
                visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return result


def render_templates(value: Any, values: dict[str, Any]) -> Any:
    if isinstance(value, str):
        exact = VAR_RE.fullmatch(value.strip())
        if exact:
            return values.get(exact.group(1), value)
        return VAR_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {key: render_templates(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [render_templates(item, values) for item in value]
    return value


def extract_jsonpath(body: Any, expression: str) -> list[Any]:
    return [match.value for match in parse_jsonpath(expression).find(body)]


def find_id_paths(value: Any, prefix: str = "$") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if key.lower().endswith("id"):
                result.setdefault(key, path)
            result.update(find_id_paths(item, path))
    elif isinstance(value, list) and value:
        result.update(find_id_paths(value[0], f"{prefix}[0]"))
    return result


def find_jsonpath_candidates(body: Any, variable_name: str) -> list[str]:
    target = re.sub(r"[^a-z0-9]", "", variable_name.lower())
    candidates: list[tuple[int, str]] = []

    def visit(node: Any, path: str, depth: int) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                next_path = f"{path}.{key}"
                field = re.sub(r"[^a-z0-9]", "", key.lower())
                score = 0
                if field == target:
                    score = 100 - depth
                elif target.endswith(field) or field.endswith(target):
                    score = 80 - depth
                elif target.endswith("id") and field == "id":
                    score = 50 - depth
                if score:
                    candidates.append((score, next_path))
                visit(item, next_path, depth + 1)
        elif isinstance(node, list) and node:
            visit(node[0], f"{path}[0]", depth + 1)

    visit(body, "$", 0)
    return [path for _, path in sorted(candidates, key=lambda item: (-item[0], len(item[1]), item[1]))]


def deep_overlay(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for key, value in patch.items():
            merged[key] = deep_overlay(merged.get(key), value)
        return merged
    return patch
