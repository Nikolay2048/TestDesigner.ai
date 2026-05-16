"""
LLM factory — creates a LangChain chat model from :class:`AppConfig`.

Supports:
* ``ollama`` — local models via Ollama (``langchain-ollama``)
* ``openai`` — OpenAI API (``langchain-openai``)

Usage::

    from src.utils.config import get_config
    from src.utils.llm_factory import create_llm

    llm = create_llm(get_config())
    response = llm.invoke("Hello")
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

from src.utils.config import AppConfig

logger = logging.getLogger(__name__)


def create_llm(config: AppConfig) -> BaseChatModel:
    """
    Instantiate and return a LangChain chat model based on *config*.

    Args:
        config: Loaded :class:`AppConfig` instance.

    Returns:
        A :class:`BaseChatModel` subclass ready for ``.invoke()`` or
        ``.with_structured_output()``.

    Raises:
        ValueError: If an unknown provider name is configured.
        ImportError: If the required LangChain integration package is missing.
    """
    provider = config.llm.provider
    logger.debug("Creating LLM for provider '%s'", provider)

    if provider == "ollama":
        from langchain_ollama import ChatOllama  # type: ignore[import]

        cfg = config.llm.ollama
        logger.info(
            "LLM: Ollama  model=%s  base_url=%s  temperature=%s",
            cfg.model, cfg.base_url, cfg.temperature,
        )
        return ChatOllama(
            base_url=cfg.base_url,
            model=cfg.model,
            temperature=cfg.temperature,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore[import]

        cfg = config.llm.openai
        logger.info(
            "LLM: OpenAI  model=%s  temperature=%s",
            cfg.model, cfg.temperature,
        )
        return ChatOpenAI(
            api_key=cfg.api_key,
            model=cfg.model,
            temperature=cfg.temperature,
        )

    raise ValueError(
        f"Unknown LLM provider: '{provider}'. "
        f"Supported values: 'ollama', 'openai'."
    )
