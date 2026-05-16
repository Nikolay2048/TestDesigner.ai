"""
Centralized logging configuration for the TestDesignerAI system.

Supports two output modes:
  - "rich"  : coloured, human-readable via the *rich* library (default for dev)
  - "plain" : standard StreamHandler suitable for CI / log collectors
"""

import logging
import os
import sys
from typing import Literal

_CONFIGURED = False

LogFormat = Literal["rich", "plain"]


def setup_logging(
    level: str = "INFO",
    fmt: LogFormat = "rich",
) -> None:
    """
    Configure root logger once for the entire process.

    Subsequent calls are no-ops so that importing order does not matter.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    log_level = getattr(logging, level.upper(), logging.INFO)

    if fmt == "rich":
        _setup_rich_handler(log_level)
    else:
        _setup_plain_handler(log_level)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def _setup_rich_handler(level: int) -> None:
    try:
        from rich.logging import RichHandler

        logging.basicConfig(
            level=level,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[
                RichHandler(
                    rich_tracebacks=True,
                    show_path=False,
                    markup=True,
                )
            ],
        )
    except ImportError:
        _setup_plain_handler(level)


def _setup_plain_handler(level: int) -> None:
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        stream=sys.stdout,
    )


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — equivalent to ``logging.getLogger(name)``."""
    return logging.getLogger(name)
