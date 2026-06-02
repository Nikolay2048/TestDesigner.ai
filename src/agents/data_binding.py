from __future__ import annotations

from domain import AgentRun, ProjectState


class DataBindingAgent:
    """Stub for the future agent that binds request fields to variables and constants."""

    name = "Data Binding"

    def run(self, state: ProjectState) -> tuple[ProjectState, AgentRun]:
        return state, AgentRun(
            agent_name=self.name,
            status="stub",
            notes=[
                "Not implemented yet.",
                "Future input: one flow step + request schema + previous response schemas + available variables.",
                "Future output: request body/path/query values and variables to save from response.",
            ],
        )

