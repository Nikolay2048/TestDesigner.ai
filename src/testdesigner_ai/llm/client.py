from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from testdesigner_ai.llm.models import (
    OutputT,
    PromptMessage,
    StructuredLLMAttempt,
    TokenUsage,
)


class StructuredLLMClient:
    """Execute one typed LLM call while preserving raw provider evidence."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        provider: str,
        model_name: str,
    ) -> None:
        self._model = model
        self._provider = provider
        self._model_name = model_name

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate(
        self,
        messages: Sequence[PromptMessage],
        output_type: type[OutputT],
    ) -> StructuredLLMAttempt[OutputT]:
        runnable = self._model.with_structured_output(
            output_type,
            method="json_schema",
            include_raw=True,
        )

        try:
            result = runnable.invoke(
                [
                    (
                        "human" if message.role == "user" else message.role,
                        message.content,
                    )
                    for message in messages
                ]
            )
        except Exception as exc:
            return StructuredLLMAttempt[output_type](
                status="provider_error",
                provider=self._provider,
                model=self._model_name,
                error=str(exc),
            )

        raw = result.get("raw")
        parsed = result.get("parsed")
        parsing_error = result.get("parsing_error")
        raw_content = _raw_content(raw)
        response_metadata = dict(raw.response_metadata) if isinstance(raw, AIMessage) else {}
        usage = _usage(raw)

        if parsing_error is not None or not isinstance(parsed, output_type):
            return StructuredLLMAttempt[output_type](
                status="schema_error",
                provider=self._provider,
                model=self._model_name,
                raw_content=raw_content,
                response_metadata=response_metadata,
                usage=usage,
                error=str(parsing_error or "Provider returned an unexpected parsed value."),
            )

        return StructuredLLMAttempt[output_type](
            status="completed",
            provider=self._provider,
            model=self._model_name,
            value=parsed,
            raw_content=raw_content,
            response_metadata=response_metadata,
            usage=usage,
        )


def _raw_content(message: Any) -> str:
    if not isinstance(message, AIMessage):
        return ""
    if isinstance(message.content, str):
        return message.content
    return str(message.content)


def _usage(message: Any) -> TokenUsage | None:
    if not isinstance(message, AIMessage) or not message.usage_metadata:
        return None
    metadata = message.usage_metadata
    return TokenUsage(
        input_tokens=metadata.get("input_tokens"),
        output_tokens=metadata.get("output_tokens"),
        total_tokens=metadata.get("total_tokens"),
    )
