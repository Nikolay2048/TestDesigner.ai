from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from pydantic import ValidationError

from testdesigner_ai.llm.factory import create_chat_model, create_llm_client
from testdesigner_ai.llm.client import StructuredLLMClient
from testdesigner_ai.llm.models import PromptMessage
from testdesigner_ai.llm.settings import LLMProvider, LLMSettings


class ExampleOutput(BaseModel):
    answer: str


class FakeRunnable:
    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.input = None

    def invoke(self, value):
        self.input = value
        if self.error:
            raise self.error
        return self.result


class FakeChatModel:
    def __init__(self, runnable: FakeRunnable):
        self.runnable = runnable
        self.structured_output_args = None

    def with_structured_output(self, schema, **kwargs):
        self.structured_output_args = (schema, kwargs)
        return self.runnable


def test_structured_llm_client_returns_typed_output_and_raw_evidence() -> None:
    raw = AIMessage(
        content='{"answer":"ok"}',
        response_metadata={"done_reason": "stop"},
        usage_metadata={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    )
    runnable = FakeRunnable(
        {
            "raw": raw,
            "parsed": ExampleOutput(answer="ok"),
            "parsing_error": None,
        }
    )
    model = FakeChatModel(runnable)
    client = StructuredLLMClient(model, provider="fake", model_name="fake-model")

    attempt = client.generate(
        [PromptMessage(role="user", content="Return an answer.")],
        ExampleOutput,
    )

    assert attempt.status == "completed"
    assert attempt.value == ExampleOutput(answer="ok")
    assert attempt.raw_content == '{"answer":"ok"}'
    assert attempt.usage.total_tokens == 14
    assert model.structured_output_args == (
        ExampleOutput,
        {"method": "json_schema", "include_raw": True},
    )
    assert runnable.input == [("human", "Return an answer.")]


def test_structured_llm_client_preserves_schema_error() -> None:
    runnable = FakeRunnable(
        {
            "raw": AIMessage(content='{"wrong":"value"}'),
            "parsed": None,
            "parsing_error": ValueError("answer is required"),
        }
    )
    client = StructuredLLMClient(
        FakeChatModel(runnable),
        provider="fake",
        model_name="fake-model",
    )

    attempt = client.generate(
        [PromptMessage(role="system", content="Return structured JSON.")],
        ExampleOutput,
    )

    assert attempt.status == "schema_error"
    assert attempt.value is None
    assert attempt.raw_content == '{"wrong":"value"}'
    assert attempt.error == "answer is required"


def test_structured_llm_client_normalizes_provider_error() -> None:
    client = StructuredLLMClient(
        FakeChatModel(FakeRunnable(error=TimeoutError("model timeout"))),
        provider="fake",
        model_name="fake-model",
    )

    attempt = client.generate(
        [PromptMessage(role="user", content="Return an answer.")],
        ExampleOutput,
    )

    assert attempt.status == "provider_error"
    assert attempt.error == "model timeout"


def test_local_provider_is_the_default_without_api_key() -> None:
    settings = LLMSettings(_env_file=None)

    assert settings.provider == LLMProvider.LOCAL
    assert settings.selected_model == "qwen3.5:27b"
    assert settings.openrouter_api_key is None


def test_ollama_factory_uses_typed_settings() -> None:
    settings = LLMSettings(
        _env_file=None,
        OLLAMA_MODEL="qwen3.5:27b",
        OLLAMA_BASE_URL="http://127.0.0.1:11434",
        LLM_TIMEOUT=120,
        LLM_TEMPERATURE=0.1,
        OLLAMA_NUM_CTX=8192,
        LLM_SEED=7,
        OLLAMA_REASONING=False,
    )

    model = create_chat_model(settings)

    assert model.model == "qwen3.5:27b"
    assert model.base_url == "http://127.0.0.1:11434"
    assert model.temperature == 0.1
    assert model.num_ctx == 8192
    assert model.seed == 7


def test_openrouter_requires_api_key_only_when_selected() -> None:
    with pytest.raises(ValidationError, match="OPENROUTER_API_KEY is required"):
        LLMSettings(_env_file=None, LLM_PROVIDER="openrouter")

    with pytest.raises(ValidationError, match="OPENROUTER_API_KEY is required"):
        LLMSettings(
            _env_file=None,
            provider="openrouter",
            openrouter_api_key="",
        )


def test_openrouter_factory_uses_api_model_and_hides_secret() -> None:
    settings = LLMSettings(
        _env_file=None,
        provider="openrouter",
        api_model="qwen/qwen3-32b",
        openrouter_api_key="test-secret",
        openrouter_base_url="https://openrouter.ai/api/v1",
        timeout=120,
    )

    model = create_chat_model(settings)

    assert settings.selected_model == "qwen/qwen3-32b"
    assert model.model_name == "qwen/qwen3-32b"
    assert str(settings.openrouter_api_key) == "**********"
    assert "test-secret" not in repr(settings)


def test_client_factory_reports_selected_provider_and_model() -> None:
    settings = LLMSettings(
        _env_file=None,
        OLLAMA_MODEL="qwen3:14b",
    )

    client = create_llm_client(settings)

    assert client.provider == "local"
    assert client.model_name == "qwen3:14b"
