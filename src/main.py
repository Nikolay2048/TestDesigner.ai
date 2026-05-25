import os

from langchain_ollama import ChatOllama

from src.agents.llm_provider import llm
from src.agents.prompts import EXECUTOR_SYSTEM_PROMPT, executor_query
from src.agents.tools import EXECUTOR_TOOLS
from src.agents.utils import create_react_agent, run_and_trace

executor_agent =create_react_agent(llm=llm, tools_list=EXECUTOR_TOOLS, system_prompt=EXECUTOR_SYSTEM_PROMPT)
print("agent created")


run_and_trace(executor_agent, executor_query)






# agent = ScenarioStabilizationAgent(
#     llm=llm,
#     max_iterations=50,
#     verbose=True,
# )
#
# result = agent.run(
#     ScenarioStabilizationInput(
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
#     )
# )

# print(result)


# print("ReAct agent created")
#
# scenario_query = build_scenario_prompt(ScenarioStabilizationInput(
#     scenario_name="Carsharing booking scenario",
#     base_url="http://localhost:8080",
#     steps=[
#         TestStep(
#             step=1,
#             name="Получение списка доступных автомобилей",
#             method="GET",
#             path="/v1/vehicles/available",
#             request_body=None,
#             query_params=None,
#             expected_status=200,
#             assert_resp=[
#                 "response.count >= 1"
#             ],
#         ),
#
#         TestStep(
#             step=2,
#             name="Создание бронирования автомобиля",
#             method="POST",
#             path="/v1/bookings",
#             request_body={
#                 "userId": "user-1",
#                 "vehicleId": "{{ availableVehicleId }}",
#                 "startDate": "{{ startDate }}"
#             },
#             query_params=None,
#             expected_status=201,
#             assert_resp=[
#                 "response.status == 'CREATED'"
#             ],
#         )
#     ],
# ))
#
# result1, steps1 = run_and_trace(react_agent, scenario_query)

# print_agent_trace(result)
