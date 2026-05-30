from typing import Optional
from pydantic import BaseModel


class ExecResult(BaseModel):
    case_id: str
    passed: bool
    steps_log: list[dict]
    stabilization_applied: list[dict] = []
    failure_reason: Optional[str] = None
