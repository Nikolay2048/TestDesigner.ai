"""
Конфигурация запуска — единая точка для параметров, зависящих от окружения.

Граф не знает о предметной области. Runtime-параметры (base_url, seed env-vars,
имя коллекции) задаются через constants.json, CLI-аргументы или напрямую.

Использование:
    from src.config import CONFIG, load_domain_config
    load_domain_config("carsharing")   # читает из constants.json
    # или вручную:
    CONFIG.base_url = "http://localhost:8000"
    CONFIG.env_vars = {"customerId": "aaa...", "paymentId": "999..."}
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunConfig:
    # URL тестируемого сервиса (или mock-сервера)
    base_url: str = field(
        default_factory=lambda: os.getenv("TEST_BASE_URL", "http://localhost:8000")
    )
    # Seed-переменные окружения (camelCase, executor делает case-tolerant поиск)
    env_vars: dict[str, str] = field(default_factory=dict)
    # Имя Postman-коллекции в экспортируемом JSON
    collection_name: str = "API Test Collection"


# Глобальный инстанс. Изменяется до вызова graph.invoke().
CONFIG = RunConfig()

_CONSTANTS_PATH = Path(__file__).parent.parent / "constants.json"


def load_constants() -> dict:
    """Загружает constants.json из корня проекта."""
    if not _CONSTANTS_PATH.exists():
        return {}
    with open(_CONSTANTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_domain_config(domain: str) -> dict:
    """
    Загружает конфигурацию домена из constants.json и применяет к CONFIG.
    Возвращает полный dict конфигурации домена (для доступа к groups/scenarios).
    """
    data = load_constants()
    domain_cfg = data.get("domains", {}).get(domain, {})
    if not domain_cfg:
        raise ValueError(f"Domain {domain!r} not found in constants.json")
    CONFIG.base_url = domain_cfg.get("base_url", CONFIG.base_url)
    CONFIG.env_vars = domain_cfg.get("env_vars", {})
    CONFIG.collection_name = domain_cfg.get("collection_name", "API Test Collection")
    return domain_cfg
