from __future__ import annotations

from langchain_openai import ChatOpenAI

from testdesigner_ai.llm.settings import LLMSettings


def create_openrouter_model(settings: LLMSettings) -> ChatOpenAI:
    if settings.openrouter_api_key is None:
        raise ValueError("OPENROUTER_API_KEY is required for OpenRouter.")

    return ChatOpenAI(
        model=settings.api_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=settings.temperature,
        timeout=settings.timeout,
        seed=settings.seed,
        max_retries=settings.max_retries,
    )
