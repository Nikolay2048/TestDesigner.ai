from __future__ import annotations

from pathlib import Path

from agents.documentation_analyst import DocumentationAnalystAgent
from domain import ProjectState, ScenarioInput
from io_utils import extract_raw_endpoint_mentions
from orchestrator import AgenticTestDesignOrchestrator


def test_raw_endpoint_mentions_are_extracted_deterministically() -> None:
    text = """
Available endpoints:
GET /locations
Use /reservations/{reservationId}/cancel later.
"""

    mentions = extract_raw_endpoint_mentions(text)

    assert [(item.method, item.path) for item in mentions] == [
        ("GET", "/locations"),
        (None, "/reservations/{reservationId}/cancel"),
    ]


def test_documentation_analyst_prompt_mentions_endpoint_capture() -> None:
    state = ProjectState(
        scenario=ScenarioInput(
            path="scenario.md",
            title="Demo",
            text="Step 1: call GET /locations",
            raw_endpoint_mentions=extract_raw_endpoint_mentions("Step 1: call GET /locations"),
        )
    )

    prompt = DocumentationAnalystAgent().build_prompt(state)

    assert "endpoint_mentions" in prompt[1].content
    assert '"path": "/locations"' in prompt[1].content
    assert "Do not add, remove, rewrite, normalize, or infer endpoints" in prompt[1].content
    assert "header|step|rule|unknown" in prompt[1].content
    assert "scenario_dependencies" in prompt[1].content
    assert "requires_scenario|requires_state|requires_data" in prompt[1].content


def test_orchestrator_without_llm_saves_documentation_prompt() -> None:
    out_dir = "runs/test_documentation_stage"

    state = AgenticTestDesignOrchestrator().run(
        scenario_path="data/carsharing/specs/01-basic-economy-rental.md",
        out_dir=out_dir,
    )

    assert state.agent_runs[0].agent_name == "Documentation Analyst"
    assert state.agent_runs[0].status == "needs_llm"
    assert Path(out_dir, "documentation_analyst.prompt.md").exists()
    assert Path(out_dir, "documentation_analyst.run.json").exists()
    assert Path(out_dir, "state.json").exists()
