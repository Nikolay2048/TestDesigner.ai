from __future__ import annotations

import pytest

from domain import AgentMessage
from llm import OllamaLLM, OpenRouterLLM


def test_ollama_llm_uses_structured_output_settings(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"status":"ok"}'}}

    def fake_post(url, *, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr("llm.httpx.post", fake_post)

    llm = OllamaLLM(
        model="qwen3.5:27b",
        timeout=321,
        temperature=0.1,
        num_ctx=32768,
        think=False,
        seed=42,
    )
    result = llm.complete([AgentMessage(role="user", content="Return JSON")])

    assert result == '{"status":"ok"}'
    assert captured["timeout"] == 321
    assert captured["json"]["think"] is False
    assert captured["json"]["options"] == {
        "temperature": 0.1,
        "num_ctx": 32768,
        "seed": 42,
    }


def test_openrouter_llm_calls_chat_completions(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"status":"ok"}'}}]}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr("llm.httpx.post", fake_post)

    llm = OpenRouterLLM(
        model="qwen/qwen3-32b",
        api_key="secret",
        timeout=42.0,
    )
    result = llm.complete([AgentMessage(role="user", content="Return JSON")])

    assert result == '{"status":"ok"}'
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 42.0
    assert captured["json"] == {
        "model": "qwen/qwen3-32b",
        "messages": [{"role": "user", "content": "Return JSON"}],
        "temperature": 0.1,
    }


@pytest.mark.parametrize(
    ("model", "api_key", "message"),
    [
        ("qwen/qwen3-32b", "", "OPENROUTER_API_KEY"),
        ("", "secret", "OPENROUTER_MODEL"),
    ],
)
def test_openrouter_llm_requires_configuration(model, api_key, message):
    with pytest.raises(ValueError, match=message):
        OpenRouterLLM(model=model, api_key=api_key)


def test_openrouter_llm_rejects_malformed_response(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": []}

    monkeypatch.setattr("llm.httpx.post", lambda *args, **kwargs: Response())

    llm = OpenRouterLLM(model="qwen/qwen3-32b", api_key="secret")
    with pytest.raises(ValueError, match="assistant message content"):
        llm.complete([AgentMessage(role="user", content="Return JSON")])
