import os

from langchain_ollama import ChatOllama

from src.agents.scenario_stabilization_agent import ScenarioStabilizationAgent, ScenarioStabilizationInput
from src.agents.utils import check_llm_connection, print_agent_trace
from src.models.executor import TestStep

MODEL_NAME = 'qwen2.5:14b-instruct'
os.environ['OPENAI_API_KEY'] = 'api key'

llm = ChatOllama(model=MODEL_NAME, temperature=0)

check_llm_connection(llm)

agent = ScenarioStabilizationAgent(
    llm=llm,
    max_iterations=50,
    verbose=True,
)

result = agent.run(
    ScenarioStabilizationInput(
        scenario_name="Carsharing booking scenario",
        base_url="http://localhost:8080",
        steps=[
            TestStep(
                step=1,
                name="Получение списка доступных автомобилей",
                method="GET",
                path="/v1/vehicles/available",
                request_body=None,
                query_params=None,
                expected_status=200,
                assert_resp=[
                    "response.count >= 1"
                ],
            ),

            TestStep(
                step=2,
                name="Создание бронирования автомобиля",
                method="POST",
                path="/v1/bookings",
                request_body={
                    "userId": "user-1",
                    "vehicleId": "{{ availableVehicleId }}",
                    "startDate": "{{ startDate }}"
                },
                query_params=None,
                expected_status=201,
                assert_resp=[
                    "response.status == 'CREATED'"
                ],
            )
        ],
    )
)

print(result)

print_agent_trace(result)
