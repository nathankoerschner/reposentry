"""Helpers for ordering findings by descending severity."""

from __future__ import annotations

from app.models.enums import Severity
from app.models.finding_occurrence import FindingOccurrence

SEVERITY_RANK = {
    Severity.critical: 0,
    Severity.high: 1,
    Severity.medium: 2,
    Severity.low: 3,
}


def sort_occurrences_by_severity_desc(
    occurrences: list[FindingOccurrence],
) -> list[FindingOccurrence]:
    """Return finding occurrences ordered from highest to lowest severity."""
    return sorted(
        occurrences,
        key=lambda occ: (
            SEVERITY_RANK.get(occ.severity, len(SEVERITY_RANK)),
            occ.file_path,
            occ.line_number,
            str(occ.id),
        ),
    )
