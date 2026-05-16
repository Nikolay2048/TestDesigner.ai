"""CLI entry point for the TestDesignerAI multi-agent pipeline."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.agents.agent1_scenario_builder import ScenarioBuilderAgent
from src.agents.agent2_executor import ExecutorAgent
from src.modules.postman_generator import PostmanGenerator
from src.modules.swagger_parser import SwaggerParser
from src.utils.config import get_config
from src.utils.logging_setup import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and optionally execute Postman scenarios from OpenAPI and SA text.")
    parser.add_argument("--scenario", default="data/scenario.md", help="Path to system-analysis scenario markdown.")
    parser.add_argument("--openapi", default="data/openapi.yaml", help="Path to root OpenAPI YAML/JSON file.")
    parser.add_argument("--constants", default=None, help="Path to constants JSON. Defaults to config paths.constants.")
    parser.add_argument("--out-dir", default="output", help="Directory for generated artifacts.")
    parser.add_argument("--plan-only", action="store_true", help="Only run parser and Agent 1.")
    parser.add_argument("--base-url", default=None, help="Override API base URL for execution.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = get_config()
    setup_logging(level=cfg.logging.level, fmt=cfg.logging.format)
    log = logging.getLogger("main")

    scenario_path = Path(args.scenario)
    openapi_path = Path(args.openapi)
    constants_path = Path(args.constants or cfg.paths.constants)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    constants = json.loads(constants_path.read_text(encoding="utf-8"))
    scenario_text = scenario_path.read_text(encoding="utf-8")

    log.info("Pipeline: parse OpenAPI %s", openapi_path)
    spec = SwaggerParser(openapi_path).parse()
    (out_dir / "openapi_catalog.json").write_text(
        spec.model_dump_json(indent=2, by_alias=True),
        encoding="utf-8",
    )

    log.info("Pipeline: run Agent 1")
    scenario = ScenarioBuilderAgent(cfg).build(scenario_text, spec, constants)
    scenario_path_out = out_dir / "scenario_card.json"
    scenario_path_out.write_text(scenario.model_dump_json(indent=2, by_alias=True), encoding="utf-8")

    postman = PostmanGenerator(reset_mock_between_scenarios=True)
    collection = postman.generate([scenario], constants)
    (out_dir / "postman_collection_from_plan.json").write_text(
        json.dumps(collection, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.plan_only:
        log.info("Pipeline: plan-only mode done -> %s", scenario_path_out)
        return 0

    base_url = args.base_url or constants.get("base_url") or spec.base_url
    log.info("Pipeline: run Agent 2 against %s", base_url)
    with ExecutorAgent(cfg, base_url) as executor:
        result = executor.run(scenario, constants)

    (out_dir / "execution_result.json").write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    executed_collection = postman.generate_from_execution(result)
    (out_dir / "postman_collection_from_execution.json").write_text(
        json.dumps(executed_collection, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    environment = postman.generate_environment(result.variables)
    (out_dir / "postman_environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Pipeline: done overall=%s", result.overall_status)
    return 0 if result.overall_status == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
