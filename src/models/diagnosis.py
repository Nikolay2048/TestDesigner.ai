from enum import Enum
from typing import Optional
from pydantic import BaseModel


class DiagnosisCategory(str, Enum):
    TEST_DATA_ISSUE = "test_data_issue"
    SPEC_GAP = "spec_gap"
    SCENARIO_CARD_ERROR = "card_error"
    SUSPECTED_SERVICE_BUG = "service_bug"
    UNCERTAIN = "uncertain"


class Diagnosis(BaseModel):
    case_id: str
    step_id: str
    what_changed: dict
    category: DiagnosisCategory
    confidence: float
    evidence: list[str]
    reasoning: str
    needs_human: bool
    human_decision: Optional[str] = None
