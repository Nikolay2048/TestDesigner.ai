"""CLI entry point for the REST test designer."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.testdesigner.config import load_config
from src.testdesigner.llm import LlmError
from src.testdesigner.logging_setup import setup_logging
from src.testdesigner.orchestrator import RestTestDesigner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and execute REST API test scenarios.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--scenario")
    parser.add_argument("--openapi")
    parser.add_argument("--constants")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    config = load_config(args.config)
    try:
        return RestTestDesigner(config).run(
            scenario_path=Path(args.scenario or config.paths.scenario),
            openapi_path=Path(args.openapi or config.paths.openapi),
            constants_path=Path(args.constants or config.paths.constants),
            execute=not args.plan_only,
        )
    except LlmError as exc:
        print(f"LLM error: {exc}")
        print("Start Ollama and install the configured model, or set llm.provider/model in config.yaml.")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
