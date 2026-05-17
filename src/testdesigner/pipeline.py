"""End-to-end orchestration pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.testdesigner.agents import PipelineGraph
from src.testdesigner.config import AppConfig
from src.testdesigner.openapi_parser import OpenApiParser
from src.testdesigner.postman import PostmanGenerator

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(self, scenario_path: Path, openapi_path: Path, constants_path: Path, execute: bool = True) -> int:
        base_out = Path(self.config.runtime.output_dir)
        base_out.mkdir(parents=True, exist_ok=True)

        scenario_dir = base_out / scenario_path.stem
        scenario_dir.mkdir(parents=True, exist_ok=True)

        constants, constant_descriptions = self._load_constants(constants_path)
        scenario_text = scenario_path.read_text(encoding="utf-8")

        catalog = OpenApiParser(openapi_path).parse()
        (scenario_dir / "openapi_catalog.json").write_text(
            catalog.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
        )

        base_url = constants.get("base_url") or catalog.base_url

        graph = PipelineGraph(self.config)
        try:
            card, report = graph.run(
                scenario_text=scenario_text,
                catalog=catalog,
                constants=constants,
                constant_descriptions=constant_descriptions,
                base_url=base_url,
                execute=execute,
            )
        finally:
            graph.close()

        (scenario_dir / "scenario_card.json").write_text(card.model_dump_json(indent=2), encoding="utf-8")

        postman = PostmanGenerator()
        (scenario_dir / "postman_collection_plan.json").write_text(
            json.dumps(postman.from_plan(card), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if report is None:
            logger.info("Pipeline: plan-only complete")
            return 0

        (scenario_dir / "execution_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (scenario_dir / "postman_collection_execution.json").write_text(
            json.dumps(postman.from_execution(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (scenario_dir / "postman_environment.json").write_text(
            json.dumps(postman.environment(report.variables), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Pipeline: complete status=%s", report.status)
        return 0 if report.status == "passed" else 2

    @staticmethod
    def _load_constants(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        constants: dict[str, Any] = {}
        descriptions: dict[str, str] = {}
        for name, value in raw.items():
            if isinstance(value, dict) and "value" in value:
                constants[name] = value["value"]
                descriptions[name] = str(value.get("description") or "")
            else:
                constants[name] = value
                descriptions[name] = ""
        return constants, descriptions
