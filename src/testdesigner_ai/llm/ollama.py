from __future__ import annotations

from langchain_ollama import ChatOllama

from testdesigner_ai.llm.settings import LLMSettings


def create_ollama_model(settings: LLMSettings) -> ChatOllama:
    return ChatOllama(
        model=settings.local_model,
        base_url=settings.ollama_base_url,
        temperature=settings.temperature,
        num_ctx=settings.ollama_num_ctx,
        seed=settings.seed,
        reasoning=settings.ollama_reasoning,
        client_kwargs={"timeout": settings.timeout},
        async_client_kwargs={"timeout": settings.timeout},
    )
