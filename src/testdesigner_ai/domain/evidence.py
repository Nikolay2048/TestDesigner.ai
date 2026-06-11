from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    """Pointer to the exact source fragment supporting a derived fact."""

    source_id: str
    locator: str
    excerpt: str | None = None


class ReviewState(BaseModel):
    """Human-review requirement attached to an uncertain result."""

    required: bool = False
    reasons: list[str] = Field(default_factory=list)
