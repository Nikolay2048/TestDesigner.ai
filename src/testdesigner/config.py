"""Configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class LlmConfig(BaseModel):
    provider: Literal["none", "ollama", "openai"] = "none"
    model: str = "qwen2.5:14b-instruct"
    base_url: str = "http://localhost:11434"
    temperature: float = 0
    context_window: int = 8192
    api_key_env: str = "OPENAI_API_KEY"


class RuntimeConfig(BaseModel):
    output_dir: str = "output"
    request_timeout_seconds: float = 30
    max_execution_rounds: int = 2
    max_attempts_per_step: int = 1


class PathConfig(BaseModel):
    constants: str = "data/constants.json"
    openapi: str = "data/openapi.yaml"
    scenario: str = "data/scenarios/UC-001_create_and_cancel.md"


class AppConfig(BaseModel):
    llm: LlmConfig = Field(default_factory=lambda: LlmConfig(provider="ollama"))
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    paths: PathConfig = Field(default_factory=PathConfig)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
