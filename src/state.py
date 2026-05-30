from typing import Annotated, TypedDict
from operator import add


class GraphState(TypedDict):
    # входы
    spec_paths: list[str]       # пути к OpenAPI YAML-файлам
    raw_scenarios: str          # текстовые постановки сценариев

    # spec_parser → список Endpoint.model_dump()
    endpoints: list[dict]

    # scenario_analyst → FlowCard.model_dump()
    flow_card: dict

    # validator
    validation_errors: Annotated[list, add]

    # executor (фаза 1: стабилизация)
    stabilized_card: dict

    # все стабилизированные потоки — накапливается через Annotated[list, add]
    # используется Stage 8 (inter-flow): test_designer строит из них setup_chain
    all_stabilized_cards: Annotated[list, add]

    # test_designer → список TestCase.model_dump()
    test_cases: list[dict]

    # executor (фаза 2: прогон всех кейсов)
    exec_results: list[dict]

    # diagnosis
    diagnoses: list[dict]

    # human_review (LangGraph interrupt)
    human_decisions: list[dict]   # решения от человека
    no_interrupt: bool             # True → пропустить interrupt (авторежим)

    # collection_builder
    collection: dict

    # reporter
    metrics: dict

    # служебное: след узлов для отладки
    trace: Annotated[list, add]
