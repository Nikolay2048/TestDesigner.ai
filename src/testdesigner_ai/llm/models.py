from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field


OutputT = TypeVar("OutputT", bound=BaseModel)


class PromptMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class StructuredLLMAttempt(BaseModel, Generic[OutputT]):
    status: Literal["completed", "provider_error", "schema_error"]
    provider: str
    model: str
    value: OutputT | None = None
    raw_content: str = ""
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    usage: TokenUsage | None = None
    error: str | None = None
