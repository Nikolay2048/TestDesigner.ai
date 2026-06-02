from __future__ import annotations

from domain import AgentRun, ProjectState


class StabilizationDiagnosticianAgent:
    """Stub for the future agent that explains failed execution traces."""

    name = "Stabilization Diagnostician"

    def run(self, state: ProjectState) -> tuple[ProjectState, AgentRun]:
        return state, AgentRun(
            agent_name=self.name,
            status="stub",
            notes=[
                "Not implemented yet.",
                "Future input: failed step + request + response + operation schema + related business rule.",
                "Future output: root cause, evidence, suggested fix, and human-review warning.",
            ],
        )

