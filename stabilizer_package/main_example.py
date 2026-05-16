from langchain_ollama import ChatOllama

from src.agents.utils import print_agent_trace
from stabilizer_package.models import ScenarioStabilizationInput, TestStep
from stabilizer_package.scenario_stabilization_agent import ScenarioStabilizationAgent


llm = ChatOllama(model="qwen2.5:14b-instruct", temperature=0)
agent = ScenarioStabilizationAgent(llm)

result = agent.run(
    ScenarioStabilizationInput(
        scenario_name="Carsharing booking scenario",
        base_url="http://localhost:8080",
        business_context="User should retrieve an available vehicle and create booking. Booking startDate must be in the future.",
        steps=[
            TestStep(
                step=1,
                name="Получение списка доступных автомобилей",
                method="GET",
                path="/v1/vehicles/available",
                expected_status=200,
                assert_resp=["response.count >= 1"],
            ),
            TestStep(
                step=2,
                name="Создание бронирования автомобиля",
                method="POST",
                path="/v1/bookings",
                request_body={
                    "userId": "user-1",
                    "vehicleId": "VehicleId",
                    "startDate": "startDate",
                },
                expected_status=201,
                assert_resp=["response.status == 'CREATED'"],
            ),
        ],
    )
)

print(print_agent_trace(result))

print(result["stabilized_result"].model_dump_json(indent=2))
