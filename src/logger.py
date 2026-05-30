"""
Централизованное логирование системы тест-дизайна.

Уровни:
  DEBUG   — детальный трейс (биндинги, HTTP тела, промпты)
  INFO    — ключевые события узлов (стабилизация, тест-кейсы, коллекция)
  WARNING — неожиданные состояния (пропущен биндинг, неизвестный step_id)
  ERROR   — сбои узлов (LLM упал, HTTP timeout)

Настройка через env-переменную LOG_LEVEL (default: INFO).
Файл лога: output/run.log (создаётся при каждом запуске, старый перезаписывается).
"""

import logging
import os
import sys
from pathlib import Path


def setup_logging(log_dir: Path | None = None, level: str | None = None) -> logging.Logger:
    """
    Настраивает глобальное логирование.

    log_dir — папка для output/run.log. Если None, только stdout.
    level   — строка уровня ("DEBUG"/"INFO"/"WARNING"). По умолчанию из LOG_LEVEL env.
    """
    level_str = level or os.getenv("LOG_LEVEL", "INFO")
    log_level = getattr(logging, level_str.upper(), logging.INFO)

    fmt = "%(asctime)s [%(levelname)-7s] %(name)-25s | %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "run.log", mode="w", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(fmt, datefmt))
        handlers.append(file_handler)

    logging.basicConfig(
        level=log_level,
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=True,
    )

    # Заглушить шумные сторонние библиотеки
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    logging.getLogger("langgraph").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    root_log = logging.getLogger("testdesigner")
    root_log.info("Logging initialized. Level: %s", level_str.upper())
    return root_log


def get_logger(name: str) -> logging.Logger:
    """Создаёт дочерний логгер в иерархии 'testdesigner.*'."""
    return logging.getLogger(f"testdesigner.{name}")
