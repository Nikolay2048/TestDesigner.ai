from enum import Enum
from pydantic import BaseModel

from src.models.flow import ScenarioStep, VariableBinding


class TestTechnique(str, Enum):
    HAPPY_PATH = "happy_path"
    EQUIVALENCE = "equivalence"
    BOUNDARY = "boundary"
    NEGATIVE = "negative"
    STATE_BASED = "state_based"


class TestCase(BaseModel):
    case_id: str
    flow_id: str
    technique: TestTechnique
    title: str
    target_step: str
    setup_chain: list[ScenarioStep]
    modified_inputs: list[VariableBinding]
    expected_status: int
    assertions: list[dict]
    group: str
