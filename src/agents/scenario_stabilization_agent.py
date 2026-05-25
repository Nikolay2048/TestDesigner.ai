import json
import os
import time

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_ollama import ChatOllama
from langgraph.constants import END, START
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from src.agents.llm_provider import llm
from src.agents.prompts import build_scenario_prompt, SCENARIO_STABILIZATION_SYSTEM_PROMPT

from src.agents.tools import SCENARIO_STABILIZATION_TOOLS
from src.agents.utils import check_llm_connection
from src.models.executor import TestStep, ScenarioStabilizationInput


# def create_react_agent(tools_list=None, system_prompt=None):
#     """Creates a ReAct agent with the given tools.
#
#     Includes an explicit THOUGHT step: if the model calls a tool
#     without writing reasoning text (common with function-calling),
#     we make an extra LLM call to extract the reasoning.
#     """
#
#     # parallel_tool_calls=False — forces the model to call one tool per step,
#     # making the TAO loop explicit: Thought → Action → Observation → Thought → ...
#     llm_with_tools = llm.bind_tools(tools_list)
#
#     def agent_node(state: MessagesState):
#         messages = state['messages']
#         # Add system prompt if not present yet
#         if not any(isinstance(msg, SystemMessage) for msg in messages):
#             messages = [SystemMessage(content=system_prompt)] + messages
#         response = llm_with_tools.invoke(messages)
#
#         if response.tool_calls and not response.content:
#             tool_info = ', '.join(tc['name'] for tc in response.tool_calls)
#             thought = llm.invoke(
#                 messages
#                 + [
#                     HumanMessage(
#                         content=f'You chose to call: {tool_info}. '
#                                 'In 1 sentence, explain why this is the right next step. '
#                                 'Reply with ONLY your reasoning, no tool calls.'
#                     )
#                 ]
#             )
#             response.content = thought.content
#
#         return {'messages': [response]}
#
#     def should_continue(state: MessagesState):
#         last_message = state['messages'][-1]
#         if last_message.tool_calls:
#             return 'tools'
#         return END
#
#     # Build the graph
#     workflow = StateGraph(MessagesState)
#     workflow.add_node('agent', agent_node)
#     workflow.add_node('tools', ToolNode(tools_list))
#
#     workflow.add_edge(START, 'agent')
#     workflow.add_conditional_edges('agent', should_continue, {'tools': 'tools', END: END})
#     workflow.add_edge('tools', 'agent')
#
#     return workflow.compile()


# react_agent = create_react_agent(tools_list=SCENARIO_STABILIZATION_TOOLS,
#                                  system_prompt=SCENARIO_STABILIZATION_SYSTEM_PROMPT)
# print('ReAct agent created')



# if __name__ == "__main__":
#     print("ReAct agent created")
#
#     scenario_query = build_scenario_prompt(ScenarioStabilizationInput(
#         scenario_name="Carsharing booking scenario",
#         base_url="http://localhost:8080",
#         steps=[
#             TestStep(
#                 step=1,
#                 name="Получение списка доступных автомобилей",
#                 method="GET",
#                 path="/v1/vehicles/available",
#                 request_body=None,
#                 query_params=None,
#                 expected_status=200,
#                 assert_resp=[
#                     "response.count >= 1"
#                 ],
#             ),
#
#             TestStep(
#                 step=2,
#                 name="Создание бронирования автомобиля",
#                 method="POST",
#                 path="/v1/bookings",
#                 request_body={
#                     "userId": "user-1",
#                     "vehicleId": "{{ availableVehicleId }}",
#                     "startDate": "{{ startDate }}"
#                 },
#                 query_params=None,
#                 expected_status=201,
#                 assert_resp=[
#                     "response.status == 'CREATED'"
#                 ],
#             )
#         ],
#     ))
#
#     result1, steps1 = run_and_trace(react_agent, scenario_query)
