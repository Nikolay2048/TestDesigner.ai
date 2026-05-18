"""Top-level REST test design orchestration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.testdesigner.config import AppConfig
from src.testdesigner.critic import CriticAgent
from src.testdesigner.data_agent import DataAgent
from src.testdesigner.executor import ExecutorAgent
from src.testdesigner.llm import LlmClient
from src.testdesigner.models import AgentLogEntry, ExecutionReport, ScenarioCard, StepExecution
from src.testdesigner.openapi import OpenApiReader
from src.testdesigner.planner import PlannerAgent
from src.testdesigner.postman import PostmanBuilder
from src.testdesigner.scenario_parser import ScenarioParser
from src.testdesigner.utils import repair_mojibake

logger = logging.getLogger(__name__)


class RestTestDesigner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logs: list[AgentLogEntry] = []
        self.llm = LlmClient(config.llm)

    def run(self, scenario_path: Path, openapi_path: Path, constants_path: Path, execute: bool = True) -> int:
        out_dir = Path(self.config.runtime.output_dir) / scenario_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)

        constants = self._load_constants(constants_path)
        scenario_text = repair_mojibake(scenario_path.read_text(encoding="utf-8"))
        parsed = ScenarioParser().parse(scenario_text)
        contract = OpenApiReader(openapi_path).read()
        base_url = constants.get("base_url") or contract.base_url

        self._log("ScenarioParser", "parse", data={"steps": len(parsed.steps), "title": parsed.title})
        self._write(out_dir / "parsed_scenario.json", parsed.model_dump())
        self._write(out_dir / "openapi_contract.json", contract.model_dump(by_alias=True))

        planner = PlannerAgent(self.llm)
        card = planner.plan(parsed, contract, constants)
        self._log("PlannerAgent", "plan", data={"steps": len(card.steps), "variables": sorted(card.variables)})
        self._write(out_dir / "scenario_card.json", card.model_dump())

        postman = PostmanBuilder()
        self._write(out_dir / "postman_collection.json", postman.from_card(card))

        report: ExecutionReport | None = None
        if execute:
            report = self._execute(card, contract, constants, base_url)
            self._write(out_dir / "execution_report.json", report.model_dump())
            self._write(out_dir / "postman_environment.json", postman.environment(report))

        self._write(out_dir / "agent_log.json", {"actions": [item.model_dump() for item in self.logs]})
        return 0 if report is None or report.status == "passed" else 2

    def _execute(self, card: ScenarioCard, contract: Any, constants: dict[str, Any], base_url: str) -> ExecutionReport:
        critic = CriticAgent(self.llm)
        patches = []
        latest_steps: list[StepExecution] = []
        latest_values = {}

        for round_index in range(self.config.runtime.max_execution_rounds):
            self._log("Orchestrator", "execution_round_start", data={"round": round_index + 1})
            data = DataAgent(constants, self.llm)
            executor = ExecutorAgent(timeout_seconds=self.config.runtime.request_timeout_seconds)
            steps: list[StepExecution] = []
            try:
                for step in card.steps:
                    generated = data.ensure_for_step(card, step.step, [step.path_params, step.query_params, step.headers, step.request_body])
                    if generated:
                        self._log("DataAgent", "generate", step=step.step, data={"variables": generated})
                    result = executor.execute_step(card, step, data, base_url)
                    steps.append(result)
                    self._log(
                        "ExecutionAgent",
                        "execute_step",
                        status=result.status,
                        step=step.step,
                        data={
                            "checks": [check.model_dump() for check in result.checks],
                            "attempts": [attempt.model_dump() for attempt in result.attempts],
                        },
                    )
                    if result.status == "failed":
                        endpoint = contract.find(step.method, step.path)
                        patch = critic.repair(card, result, endpoint)
                        self._log(
                            "CriticAgent",
                            "analyze_failure",
                            status="patched" if patch else "no_patch",
                            step=step.step,
                            message=patch.reason if patch else result.error or "No safe patch",
                            data={"patch": patch.model_dump() if patch else None},
                        )
                        if patch:
                            patches.append(patch)
                        break
                latest_values = data.values
            finally:
                executor.close()

            if steps and all(step.status == "passed" for step in steps) and len(steps) == len(card.steps):
                report = ExecutionReport(
                    scenario_name=card.name,
                    status="passed",
                    steps=steps,
                    runtime_values=latest_values,
                    patches=patches,
                    agent_log=self.logs,
                )
                self._log("Orchestrator", "execution_complete", status="passed")
                return report
            latest_steps = self._with_skipped(card, steps)
            if not patches or not patches[-1].applied:
                break

        self._log("Orchestrator", "execution_complete", status="failed")
        return ExecutionReport(
            scenario_name=card.name,
            status="failed",
            steps=latest_steps,
            runtime_values=latest_values,
            patches=patches,
            agent_log=self.logs,
        )

    @staticmethod
    def _with_skipped(card: ScenarioCard, steps: list[StepExecution]) -> list[StepExecution]:
        executed = {step.step for step in steps}
        result = list(steps)
        for step in card.steps:
            if step.step not in executed:
                result.append(StepExecution(step=step.step, name=step.name, status="skipped", error="Previous step failed"))
        return result

    def _log(
        self,
        agent: str,
        action: str,
        status: str = "ok",
        step: int | None = None,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        logger.info("%s.%s step=%s status=%s", agent, action, step, status)
        self.logs.append(AgentLogEntry(
            agent=agent,
            action=action,
            status=status,
            step=step,
            message=message,
            data=data or {},
        ))

    @staticmethod
    def _load_constants(path: Path) -> dict[str, Any]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        result: dict[str, Any] = {}
        for key, value in raw.items():
            result[key] = value["value"] if isinstance(value, dict) and "value" in value else value
        return result

    @staticmethod
    def _write(path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
