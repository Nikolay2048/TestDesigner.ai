"""End-to-end orchestration pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.testdesigner.agents import ExecutorAgent, ScenarioBuilderAgent
from src.testdesigner.config import AppConfig
from src.testdesigner.openapi_parser import OpenApiParser
from src.testdesigner.postman import PostmanGenerator

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(self, scenario_path: Path, openapi_path: Path, constants_path: Path, execute: bool = True) -> int:
        out_dir = Path(self.config.runtime.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        constants = json.loads(constants_path.read_text(encoding="utf-8"))
        scenario_text = scenario_path.read_text(encoding="utf-8")

        catalog = OpenApiParser(openapi_path).parse()
        (out_dir / "openapi_catalog.json").write_text(catalog.model_dump_json(indent=2, by_alias=True), encoding="utf-8")

        card = ScenarioBuilderAgent(self.config).build(scenario_text, catalog, constants)
        (out_dir / "scenario_card.json").write_text(card.model_dump_json(indent=2), encoding="utf-8")

        postman = PostmanGenerator()
        (out_dir / "postman_collection_plan.json").write_text(json.dumps(postman.from_plan(card), ensure_ascii=False, indent=2), encoding="utf-8")

        if not execute:
            logger.info("Pipeline: plan-only complete")
            return 0

        base_url = constants.get("base_url") or catalog.base_url
        executor = ExecutorAgent(self.config)
        try:
            report = executor.execute(card, base_url)
        finally:
            executor.close()

        (out_dir / "execution_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "postman_collection_execution.json").write_text(json.dumps(postman.from_execution(report), ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "postman_environment.json").write_text(json.dumps(postman.environment(report.variables), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Pipeline: complete status=%s", report.status)
        return 0 if report.status == "passed" else 2
