"""CLI for TestDesignerAI."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.testdesigner.config import load_config
from src.testdesigner.logging_setup import setup_logging
from src.testdesigner.pipeline import Pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Postman collections from system-analysis scenarios and OpenAPI.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--openapi", default=None)
    parser.add_argument("--constants", default=None)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    setup_logging()
    return Pipeline(config).run(
        scenario_path=Path(args.scenario or config.paths.scenario),
        openapi_path=Path(args.openapi or config.paths.openapi),
        constants_path=Path(args.constants or config.paths.constants),
        execute=not args.plan_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
