from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from testdesigner_ai.llm.client import StructuredLLMClient
from testdesigner_ai.llm.ollama import create_ollama_model
from testdesigner_ai.llm.openrouter import create_openrouter_model
from testdesigner_ai.llm.settings import LLMProvider, LLMSettings


def create_chat_model(settings: LLMSettings) -> BaseChatModel:
    if settings.provider == LLMProvider.LOCAL:
        return create_ollama_model(settings)
    if settings.provider == LLMProvider.OPENROUTER:
        return create_openrouter_model(settings)
    raise ValueError(f"Unsupported LLM provider: {settings.provider}")


def create_llm_client(
    settings: LLMSettings | None = None,
) -> StructuredLLMClient:
    config = settings or LLMSettings()
    return StructuredLLMClient(
        create_chat_model(config),
        provider=config.provider.value,
        model_name=config.selected_model,
    )
