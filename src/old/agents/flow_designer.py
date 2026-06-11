from __future__ import annotations

from old.domain import AgentRun, FlowDraft, ProjectState


class FlowDesignerAgent:
    """Stub for the future agent that maps business steps to REST operations."""

    name = "Flow Designer"

    def run(self, state: ProjectState) -> tuple[ProjectState, AgentRun]:
        state.flow = FlowDraft(
            notes=[
                "Stub only. Later this agent will choose REST endpoints for business steps.",
                "Step order should mostly come from Documentation Analyst business_steps.",
            ]
        )
        return state, AgentRun(
            agent_name=self.name,
            status="stub",
            notes=[
                "Not implemented yet.",
                "Future input: business_steps + small OpenAPI/RAG context.",
                "Future output: ordered REST flow draft.",
            ],
        )

