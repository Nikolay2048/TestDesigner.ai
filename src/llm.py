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
    def __init__(self, model: str, base_url: str = "http://localhost:11434", timeout: float = 120.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, messages: list[AgentMessage]) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [message.model_dump() for message in messages],
        }
        response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()["message"]["content"]


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

