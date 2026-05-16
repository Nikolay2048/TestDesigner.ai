from __future__ import annotations

from typing import Any, Dict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.constants import END, START
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from .data_generation_agent import DataGenerationAgent
from .models import ScenarioStabilizationInput, StabilizedScenarioResult
from .prompts import SCENARIO_STABILIZATION_SYSTEM_PROMPT, build_scenario_prompt
from .runtime_state import RuntimeState
from .tools import create_scenario_tools


class ScenarioStabilizationAgent:
    """ReAct-style agent with Python-owned runtime state."""

    def __init__(self, llm, max_iterations: int = 80):
        self.llm = llm
        self.max_iterations = max_iterations

    def run(self, input_data: ScenarioStabilizationInput) -> Dict[str, Any]:
        state = RuntimeState(
            scenario_name=input_data.scenario_name,
            execution_context=input_data.execution_context,
        )
        data_agent = DataGenerationAgent(self.llm)
        tools = create_scenario_tools(state, data_agent)
        graph = self._create_react_graph(tools)

        result = graph.invoke(
            {"messages": [HumanMessage(content=build_scenario_prompt(input_data))]},
            config={"recursion_limit": self.max_iterations},
        )

        return {
            "messages": result["messages"],
            "runtime_state": state,
            "stabilized_result": state.build_result(),
        }

    def _create_react_graph(self, tools):
        llm_with_tools = self.llm.bind_tools(tools)

        def agent_node(state: MessagesState):
            messages = state["messages"]
            if not any(isinstance(msg, SystemMessage) for msg in messages):
                messages = [SystemMessage(content=SCENARIO_STABILIZATION_SYSTEM_PROMPT)] + messages
            response = llm_with_tools.invoke(messages)
            return {"messages": [response]}

        def should_continue(state: MessagesState):
            last_message = state["messages"][-1]
            if getattr(last_message, "tool_calls", None):
                return "tools"
            return END

        workflow = StateGraph(MessagesState)
        workflow.add_node("agent", agent_node)
        workflow.add_node("tools", ToolNode(tools))
        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
        workflow.add_edge("tools", "agent")
        return workflow.compile()
