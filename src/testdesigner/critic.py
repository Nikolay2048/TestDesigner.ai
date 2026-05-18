"""LLM-first critic and repair agent."""

from __future__ import annotations

from typing import Any

from src.testdesigner.llm import LlmClient, LlmError
from src.testdesigner.models import EndpointSpec, ScenarioCard, ScenarioPatch, StepExecution

CRITIC_SYSTEM = """
You are CriticAgent for a REST API test designer.

Analyze a failed REST step and propose exactly one high-confidence patch to the ScenarioCard.
Prefer fixing:
- request body fields;
- path/query/header templates;
- extraction JSONPath;
- expected status only if scenario text clearly expects the actual status.

Do not invent unrelated steps.
If no safe patch exists, return {"patch": null, "reason": "..."}.

Return JSON:
{
  "patch": {
    "step": 3,
    "target": "request_body" | "query_params" | "path_params" | "headers" | "expected_status" | "extract.<name>.expression",
    "after": {},
    "reason": "..."
  }
}
"""


class CriticAgent:
    def __init__(self, llm: LlmClient) -> None:
        if not llm.enabled:
            raise LlmError("CriticAgent requires an enabled LLM provider")
        self.llm = llm

    def repair(self, card: ScenarioCard, failed: StepExecution, endpoint: EndpointSpec | None) -> ScenarioPatch | None:
        step = next((item for item in card.steps if item.step == failed.step), None)
        if step is None or not failed.attempts:
            return None
        attempt = failed.attempts[-1]
        payload = {
            "scenario_card": card.model_dump(),
            "failed_step": step.model_dump(),
            "execution": failed.model_dump(),
            "last_attempt": attempt.model_dump(),
            "openapi_endpoint": endpoint.model_dump(by_alias=True) if endpoint else None,
        }
        raw = self.llm.json(CRITIC_SYSTEM, payload)
        patch_data = raw.get("patch")
        if not patch_data:
            return None
        target = patch_data.get("target")
        before = self._current_value(step, target)
        patch = ScenarioPatch(
            step=step.step,
            target=target,
            before=before,
            after=patch_data.get("after"),
            reason=patch_data.get("reason") or raw.get("reason") or "LLM repair",
            applied=False,
        )
        patch.applied = self._apply(step, patch)
        return patch

    @staticmethod
    def _current_value(step: Any, target: str) -> Any:
        if target == "request_body":
            return step.request_body
        if target == "query_params":
            return step.query_params
        if target == "path_params":
            return step.path_params
        if target == "headers":
            return step.headers
        if target == "expected_status":
            return step.expected_status
        if target.startswith("extract.") and target.endswith(".expression"):
            name = target.split(".")[1]
            for rule in step.extract:
                if rule.name == name:
                    return rule.expression
        return None

    @staticmethod
    def _apply(step: Any, patch: ScenarioPatch) -> bool:
        if patch.target == "request_body" and isinstance(patch.after, (dict, list)):
            step.request_body = patch.after
            return True
        if patch.target == "query_params" and isinstance(patch.after, dict):
            step.query_params = patch.after
            return True
        if patch.target == "path_params" and isinstance(patch.after, dict):
            step.path_params = patch.after
            return True
        if patch.target == "headers" and isinstance(patch.after, dict):
            step.headers = patch.after
            return True
        if patch.target == "expected_status" and isinstance(patch.after, int):
            step.expected_status = patch.after
            return True
        if patch.target.startswith("extract.") and patch.target.endswith(".expression") and isinstance(patch.after, str):
            name = patch.target.split(".")[1]
            for rule in step.extract:
                if rule.name == name:
                    rule.expression = patch.after
                    return True
        return False
