"""
Project configuration loader.

Reads *config.yaml* from the project root, expands ``${ENV_VAR}`` placeholders,
and exposes typed accessors for each subsystem.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Literal, Optional

import yaml
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------

ProviderType = Literal["ollama", "openai"]
LogFormat = Literal["rich", "plain"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:14b-instruct"
    temperature: float = 0.0


class OpenAIConfig(BaseModel):
    api_key: Optional[str] = None
    model: str = "gpt-4o"
    temperature: float = 0.0


class LLMConfig(BaseModel):
    provider: ProviderType = "ollama"
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)


class PathsConfig(BaseModel):
    constants: str = "data/constants.json"


class Agent2Config(BaseModel):
    max_retries_per_step: int = 3


class LoggingConfig(BaseModel):
    level: LogLevel = "INFO"
    format: LogFormat = "rich"


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    agent2: Agent2Config = Field(default_factory=Agent2Config)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand ``${VAR}`` placeholders in string values."""
    if isinstance(obj, str):
        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))
        return _ENV_PATTERN.sub(replacer, obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def load_config(config_path: Optional[str | Path] = None) -> AppConfig:
    """
    Load and validate *config.yaml*.

    Searches for the file relative to the project root (the directory containing
    this file's grandparent).  An explicit *config_path* overrides discovery.
    """
    if config_path is None:
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / "config.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        return AppConfig()

    with open(config_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    expanded = _expand_env_vars(raw)
    return AppConfig(**expanded)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Return the singleton config, loading it on first access."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
