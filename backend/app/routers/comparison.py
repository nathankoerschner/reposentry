"""Scan comparison endpoint."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.finding_occurrence import FindingOccurrence
from app.models.repository import Repository
from app.models.scan import Scan
from app.models.user import User
from app.schemas.comparison import ComparisonResponse
from app.services.github_deeplink import enrich_findings_with_deeplinks
from app.services.severity_sorting import sort_occurrences_by_severity_desc

router = APIRouter(tags=["comparison"])


@router.get("/api/repositories/{repository_id}/compare", response_model=ComparisonResponse)
async def compare_scans(
    repository_id: uuid.UUID,
    base_scan_id: uuid.UUID = Query(..., description="The older / baseline scan"),
    target_scan_id: uuid.UUID = Query(..., description="The newer / target scan"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Compare two scans for the same repository. Returns new, fixed, and persisting findings."""
    # Verify repo ownership
    repo = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == user.id).first()
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    # Verify both scans belong to this repository
    base_scan = db.query(Scan).filter(Scan.id == base_scan_id, Scan.repository_id == repository_id).first()
    target_scan = db.query(Scan).filter(Scan.id == target_scan_id, Scan.repository_id == repository_id).first()
    if not base_scan or not target_scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both scans not found for this repository",
        )

    # Load occurrences keyed by finding_identity_id
    base_occurrences = db.query(FindingOccurrence).filter(FindingOccurrence.scan_id == base_scan_id).all()
    target_occurrences = db.query(FindingOccurrence).filter(FindingOccurrence.scan_id == target_scan_id).all()

    base_identity_ids = {occ.finding_identity_id for occ in base_occurrences}
    target_identity_ids = {occ.finding_identity_id for occ in target_occurrences}

    new_ids = target_identity_ids - base_identity_ids
    fixed_ids = base_identity_ids - target_identity_ids
    persisting_ids = base_identity_ids & target_identity_ids

    new_findings_raw = [occ for occ in target_occurrences if occ.finding_identity_id in new_ids]
    fixed_findings_raw = [occ for occ in base_occurrences if occ.finding_identity_id in fixed_ids]
    persisting_findings_raw = [occ for occ in target_occurrences if occ.finding_identity_id in persisting_ids]

    return ComparisonResponse(
        base_scan_id=base_scan_id,
        target_scan_id=target_scan_id,
        new_findings=enrich_findings_with_deeplinks(
            sort_occurrences_by_severity_desc(new_findings_raw),
            target_scan,
            repo,
        ),
        fixed_findings=enrich_findings_with_deeplinks(
            sort_occurrences_by_severity_desc(fixed_findings_raw),
            base_scan,
            repo,
        ),
        persisting_findings=enrich_findings_with_deeplinks(
            sort_occurrences_by_severity_desc(persisting_findings_raw),
            target_scan,
            repo,
        ),
    )
