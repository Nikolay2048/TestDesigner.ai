from langgraph.graph import StateGraph, START, END

from src.state import GraphState
from src.nodes import (
    spec_parser,
    scenario_analyst,
    validator,
    executor_stabilize,
    diagnosis,
    test_designer,
    executor_run_all,
    collection_builder,
    reporter,
)
from src.nodes.human_review import human_review


def _stabilization_ok(state: GraphState) -> str:
    """Условное ребро: стабилизация прошла?"""
    return "ok" if state.get("stabilized_card", {}).get("is_stabilized") else "failed"


def build_graph(checkpointer=None):
    """
    Собирает граф.

    checkpointer — LangGraph checkpointer для поддержки interrupt/resume
                   (human-in-the-loop). Если None — human_review работает
                   в автоматическом режиме (пропускает interrupt).
    """
    g = StateGraph(GraphState)

    g.add_node("spec_parser", spec_parser)
    g.add_node("scenario_analyst", scenario_analyst)
    g.add_node("validator", validator)
    g.add_node("executor_stabilize", executor_stabilize)
    g.add_node("diagnosis", diagnosis)
    g.add_node("human_review", human_review)
    g.add_node("test_designer", test_designer)
    g.add_node("executor_run_all", executor_run_all)
    g.add_node("collection_builder", collection_builder)
    g.add_node("reporter", reporter)

    g.add_edge(START, "spec_parser")
    g.add_edge("spec_parser", "scenario_analyst")
    g.add_edge("scenario_analyst", "validator")
    g.add_edge("validator", "executor_stabilize")

    g.add_conditional_edges(
        "executor_stabilize",
        _stabilization_ok,
        {"ok": "diagnosis", "failed": "reporter"},
    )

    g.add_edge("diagnosis", "human_review")
    g.add_edge("human_review", "test_designer")
    g.add_edge("test_designer", "executor_run_all")
    g.add_edge("executor_run_all", "collection_builder")
    g.add_edge("collection_builder", "reporter")
    g.add_edge("reporter", END)

    return g.compile(checkpointer=checkpointer)
