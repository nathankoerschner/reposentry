"""Scan comparison response schemas."""

import uuid

from pydantic import BaseModel

from app.schemas.findings import FindingOccurrenceResponse


class ComparisonFinding(BaseModel):
    """A finding annotated with its comparison category."""

    category: str  # "new" | "fixed" | "persisting"
    occurrence: FindingOccurrenceResponse


class ComparisonResponse(BaseModel):
    base_scan_id: uuid.UUID
    target_scan_id: uuid.UUID
    new_findings: list[FindingOccurrenceResponse]
    fixed_findings: list[FindingOccurrenceResponse]
    persisting_findings: list[FindingOccurrenceResponse]
