from __future__ import annotations

from enum import StrEnum

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(StrEnum):
    LOCAL = "local"
    OPENROUTER = "openrouter"


class LLMSettings(BaseSettings):
    """Application LLM configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    provider: LLMProvider = Field(
        default=LLMProvider.LOCAL,
        validation_alias="LLM_PROVIDER",
    )

    local_model: str = Field(
        default="qwen3.5:27b",
        validation_alias=AliasChoices("OLLAMA_MODEL", "LOCAL_MODEL"),
    )
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias="OLLAMA_BASE_URL",
    )
    ollama_num_ctx: int = Field(default=32768, validation_alias="OLLAMA_NUM_CTX")
    ollama_reasoning: bool = Field(default=False, validation_alias="OLLAMA_REASONING")

    api_model: str = Field(
        default="qwen/qwen3-32b",
        validation_alias=AliasChoices("OPENROUTER_MODEL", "API_MODEL"),
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias="OPENROUTER_BASE_URL",
    )

    temperature: float = Field(default=0.1, validation_alias="LLM_TEMPERATURE")
    timeout: float = Field(default=900.0, validation_alias="LLM_TIMEOUT")
    seed: int = Field(default=42, validation_alias="LLM_SEED")
    max_retries: int = Field(default=2, validation_alias="LLM_MAX_RETRIES")

    @model_validator(mode="after")
    def require_api_key_for_openrouter(self) -> LLMSettings:
        has_api_key = (
            self.openrouter_api_key is not None
            and bool(self.openrouter_api_key.get_secret_value().strip())
        )
        if self.provider == LLMProvider.OPENROUTER and not has_api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter."
            )
        return self

    @property
    def selected_model(self) -> str:
        if self.provider == LLMProvider.LOCAL:
            return self.local_model
        return self.api_model
