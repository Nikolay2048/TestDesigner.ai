"""Configuration loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class LlmConfig(BaseModel):
    provider: Literal["none", "ollama", "openai"] = "none"
    model: str = "qwen2.5:14b-instruct"
    base_url: str = "http://localhost:11434"
    temperature: float = 0
    api_key_env: str = "OPENAI_API_KEY"


class RuntimeConfig(BaseModel):
    max_attempts_per_step: int = 5
    request_timeout_seconds: float = 30
    output_dir: str = "output"


class PathsConfig(BaseModel):
    constants: str = "data/constants.json"
    openapi: str = "data/openapi.yaml"
    scenario: str = "data/scenario.md"


class AppConfig(BaseModel):
    llm: LlmConfig = Field(default_factory=LlmConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()
    with open(config_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    raw = _expand_env(raw)
    return AppConfig.model_validate(raw)


def _expand_env(value):
    if isinstance(value, str):
        for key, env_value in os.environ.items():
            value = value.replace("${" + key + "}", env_value)
        return value
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def create_llm(config: LlmConfig):
    if config.provider == "none":
        return None
    if config.provider == "ollama":
        from langchain_ollama import ChatOllama  # type: ignore

        return ChatOllama(model=config.model, base_url=config.base_url, temperature=config.temperature)
    if config.provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore

        return ChatOpenAI(model=config.model, api_key=os.environ.get(config.api_key_env), temperature=config.temperature)
    raise ValueError(f"Unsupported LLM provider: {config.provider}")
