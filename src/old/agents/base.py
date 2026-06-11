from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from old.domain import AgentMessage, AgentRun, ProjectState
from old.llm import LLM, NoLLM, extract_json


class Agent(ABC):
    """Common LLM-agent wrapper: prompt -> JSON -> validated output."""

    name: str
    output_model: type[Any]

    def __init__(self, llm: LLM | None = None):
        self.llm = llm or NoLLM()

    def run(self, state: ProjectState) -> tuple[ProjectState, AgentRun]:
        # Concrete agents only build prompts and apply validated outputs.
        # Network calls, JSON parsing, and validation live here.
        prompt = self.build_prompt(state)
        run = AgentRun(agent_name=self.name, prompt=prompt)

        try:
            raw = self.llm.complete(prompt)
        except RuntimeError as exc:
            run.status = "needs_llm"
            run.notes.append(str(exc))
            return state, run

        try:
            parsed = extract_json(raw)
            output = self.output_model.model_validate(parsed)
        except (ValueError, ValidationError) as exc:
            run.status = "failed"
            run.notes.append(str(exc))
            run.output = {"raw": raw}
            return state, run

        run.status = "completed"
        run.output = output.model_dump(mode="json")
        return self.apply_output(state, output), run

    @abstractmethod
    def build_prompt(self, state: ProjectState) -> list[AgentMessage]:
        raise NotImplementedError

    @abstractmethod
    def apply_output(self, state: ProjectState, output: Any) -> ProjectState:
        raise NotImplementedError
