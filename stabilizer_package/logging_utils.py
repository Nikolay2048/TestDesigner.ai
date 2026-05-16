

import json
from typing import Any


def pretty(obj: Any) -> str:
    try:
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump()
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(obj)


def log_section(title: str) -> None:
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)


def log_data(title: str, data: Any) -> None:
    print("\n" + "-" * 100)
    print(title)
    print("-" * 100)
    print(pretty(data))