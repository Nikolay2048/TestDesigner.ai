from __future__ import annotations

from pathlib import Path

from agents import DocumentationAnalystAgent, EndpointMapperAgent
from domain import ProjectState
from io_utils import ArtifactStore, load_scenario
from llm import LLM
from openapi import load_openapi_operations
from validators import validate_endpoint_mapping


class AgenticTestDesignOrchestrator:
    """Runs the currently implemented learning stage."""

    def __init__(self, llm: LLM | None = None):
        self.documentation_analyst = DocumentationAnalystAgent(llm)
        self.endpoint_mapper = EndpointMapperAgent(llm)

    def run(self, scenario_path: str, openapi_path: str, out_dir: str | Path) -> ProjectState:
        state = ProjectState(
            scenario=load_scenario(scenario_path),
            operations=load_openapi_operations(openapi_path),
        )
        store = ArtifactStore(out_dir)

        # Stage 1: understand the human-written scenario.
        state, run = self.documentation_analyst.run(state)
        state.agent_runs.append(run)
        store.save_agent_run(run)
        if run.status != "completed":
            store.save_state(state)
            return state

        # Stage 2: map extracted business steps to available OpenAPI operations.
        state, run = self.endpoint_mapper.run(state)
        if state.endpoint_mapping:
            state.endpoint_mapping = validate_endpoint_mapping(state.endpoint_mapping, state.operations)
            if run.output is not None:
                run.output = state.endpoint_mapping.model_dump(mode="json")
        state.agent_runs.append(run)
        store.save_agent_run(run)

        store.save_state(state)
        return state
