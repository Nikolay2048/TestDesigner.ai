from __future__ import annotations

from domain import AgentRun, ProjectState


class TestDesignerAgent:
    """Stub for future test-case generation after happy path stabilization."""

    name = "Test Designer"

    def run(self, state: ProjectState) -> tuple[ProjectState, AgentRun]:
        return state, AgentRun(
            agent_name=self.name,
            status="stub",
            notes=[
                "Not implemented yet.",
                "Future input: stable happy path + checks + business rules + review notes.",
                "Future output: test cases by test-design techniques.",
            ],
        )

