"""Small LLM client used by agents."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from src.testdesigner.config import LlmConfig


class LlmError(RuntimeError):
    pass


class LlmClient:
    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.provider != "none"

    def json(self, system: str, user: dict[str, Any], timeout: float = 180) -> dict[str, Any]:
        if self.config.provider == "none":
            raise LlmError("LLM provider is disabled")
        if self.config.provider == "ollama":
            return self._ollama_json(system, user, timeout)
        if self.config.provider == "openai":
            return self._openai_json(system, user, timeout)
        raise LlmError(f"Unsupported provider: {self.config.provider}")

    def _ollama_json(self, system: str, user: dict[str, Any], timeout: float) -> dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=2)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": self.config.temperature, "num_ctx": self.config.context_window},
        }
        try:
            response = httpx.post(
                self.config.base_url.rstrip("/") + "/api/chat",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmError(f"Ollama request failed: {exc}") from exc
        content = response.json().get("message", {}).get("content", "")
        return self._parse_json(content)

    def _openai_json(self, system: str, user: dict[str, Any], timeout: float) -> dict[str, Any]:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise LlmError(f"Missing API key env: {self.config.api_key_env}")
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=2)},
            ],
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmError(f"OpenAI request failed: {exc}") from exc
        content = response.json()["choices"][0]["message"]["content"]
        return self._parse_json(content)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if not match:
                raise LlmError(f"LLM returned non-JSON content: {content[:300]}")
            value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise LlmError("LLM JSON response must be an object")
        return value
