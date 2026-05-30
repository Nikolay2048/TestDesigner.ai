from enum import Enum
from typing import Optional
from pydantic import BaseModel


class VarSource(str, Enum):
    GENERATED = "generated"
    FROM_STEP = "from_step"
    FROM_FLOW = "from_flow"
    STATIC = "static"
    ENV = "env"


class VariableBinding(BaseModel):
    name: str
    source: VarSource
    generator: Optional[str] = None
    source_ref: Optional[str] = None
    source_field: Optional[str] = None
    value: Optional[str] = None
    target_location: Optional[str] = None


class ScenarioStep(BaseModel):
    step_id: str
    operation_id: str
    inputs: list[VariableBinding]
    produces: list[str]
    depends_on: list[str] = []


class FlowCard(BaseModel):
    flow_id: str
    name: str
    description: str
    requires_flows: list[str] = []
    steps: list[ScenarioStep]
    exports: list[str] = []
    teardown_steps: list[ScenarioStep] = []
    is_stabilized: bool = False
    stabilization_log: list[dict] = []
