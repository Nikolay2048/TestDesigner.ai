from __future__ import annotations

import json
import re
from typing import Protocol

import httpx

from domain import AgentMessage


class LLM(Protocol):
    def complete(self, messages: list[AgentMessage]) -> str:
        ...


class NoLLM:
    def complete(self, messages: list[AgentMessage]) -> str:
        raise RuntimeError("No LLM configured. Use this run to inspect generated prompts.")


class OllamaLLM:
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout: float = 900.0,
        temperature: float = 0.1,
        num_ctx: int = 32768,
        think: bool = False,
        seed: int = 42,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.think = think
        self.seed = seed

    def complete(self, messages: list[AgentMessage]) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [message.model_dump() for message in messages],
            "think": self.think,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "seed": self.seed,
            },
        }
        response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()["message"]["content"]


class OpenRouterLLM:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 300.0,
        temperature: float = 0.1,
    ):
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for the OpenRouter LLM backend.")
        if not model:
            raise ValueError("OPENROUTER_MODEL is required for the OpenRouter LLM backend.")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature

    def complete(self, messages: list[AgentMessage]) -> str:
        payload = {
            "model": self.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": self.temperature,
        }
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("OpenRouter response does not contain assistant message content.") from exc


def extract_json(text: str) -> dict | list:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    starts = [(text.find("{"), "{", "}"), (text.find("["), "[", "]")]
    starts = [item for item in starts if item[0] != -1]
    starts.sort(key=lambda item: item[0])

    for start, open_char, close_char in starts:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == open_char:
                depth += 1
            elif text[index] == close_char:
                depth -= 1
            if depth == 0:
                return json.loads(text[start:index + 1])

    raise ValueError("LLM response does not contain valid JSON")
