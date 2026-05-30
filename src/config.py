"""
Конфигурация запуска — единая точка для параметров, зависящих от окружения.

Граф не знает о предметной области. Runtime-параметры (base_url, seed env-vars,
имя коллекции) задаются в main.py или через env-переменные, а не хардкодятся
внутри узлов графа.

Использование:
    from src.config import CONFIG
    CONFIG.base_url = "http://localhost:8000"
    CONFIG.env_vars = {"customerId": "aaa...", "paymentId": "999..."}
"""
import os
from dataclasses import dataclass, field


@dataclass
class RunConfig:
    # URL тестируемого сервиса (или mock-сервера)
    base_url: str = field(
        default_factory=lambda: os.getenv("TEST_BASE_URL", "http://localhost:8000")
    )
    # Seed-переменные окружения: customerId, paymentId и любые другие,
    # которые scenario_analyst помечает как source=ENV.
    # Ключи в camelCase — executor делает case-tolerant поиск.
    env_vars: dict[str, str] = field(default_factory=dict)
    # Имя Postman-коллекции в экспортируемом JSON
    collection_name: str = "API Test Collection"


# Глобальный инстанс. main.py изменяет его до вызова graph.invoke().
CONFIG = RunConfig()
