"""Scan endpoints."""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models.enums import ScanStatus
from app.models.finding_occurrence import FindingOccurrence
from app.models.repository import Repository
from app.models.scan import Scan
from app.models.scan_file import ScanFile
from app.models.user import User
from app.schemas.findings import FindingOccurrenceResponse
from app.schemas.scans import ScanFileResponse, ScanResponse, ScanSummaryResponse
from app.services.github_deeplink import enrich_findings_with_deeplinks
from app.services.severity_sorting import sort_occurrences_by_severity_desc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scans"])


def _get_user_repository(db: Session, repository_id: uuid.UUID, user: User) -> Repository:
    """Helper – fetch repo owned by user or raise 404."""
    repo = (
        db.query(Repository)
        .filter(Repository.id == repository_id, Repository.user_id == user.id)
        .first()
    )
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repo


def _get_user_scan(db: Session, scan_id: uuid.UUID, user: User) -> Scan:
    """Helper – fetch scan owned by user via repository or raise 404."""
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    _get_user_repository(db, scan.repository_id, user)
    return scan


def _notify_worker(scan_id: uuid.UUID) -> None:
    """Send scan job to the worker. In dev, calls worker directly; in prod, uses Pub/Sub."""
    if settings.environment == "development":
        _notify_worker_direct(scan_id)
    else:
        _publish_scan_job_pubsub(scan_id)


def _notify_worker_direct(scan_id: uuid.UUID) -> None:
    """Call the worker's /scan/direct endpoint for local development."""
    import httpx

    worker_url = getattr(settings, "worker_url", "http://localhost:8001")
    try:
        resp = httpx.post(
            f"{worker_url}/scan/direct",
            json={"scan_id": str(scan_id)},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Dispatched scan %s directly to worker: %s", scan_id, resp.json())
    except Exception:
        logger.warning("Failed to dispatch scan %s to worker directly", scan_id, exc_info=True)


def _publish_scan_job_pubsub(scan_id: uuid.UUID) -> None:
    """Publish a scan job message to Pub/Sub (production path)."""
    try:
        from google.cloud import pubsub_v1

        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(settings.gcp_project_id, settings.pubsub_topic_id)
        data = json.dumps({"scan_id": str(scan_id)}).encode("utf-8")
        future = publisher.publish(topic_path, data)
        future.result(timeout=10)
        logger.info("Published scan job %s to Pub/Sub", scan_id)
    except Exception:
        logger.warning("Failed to publish scan job %s to Pub/Sub (may be expected in dev)", scan_id, exc_info=True)


# ── Repository-scoped scan endpoints ──────────────────────────────────


@router.post(
    "/api/repositories/{repository_id}/scans",
    response_model=ScanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_scan(
    repository_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new scan job and enqueue it."""
    repo = _get_user_repository(db, repository_id, user)

    scan = Scan(repository_id=repo.id, status=ScanStatus.queued)
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # Enqueue async – don't fail the request if worker/Pub/Sub is unavailable
    _notify_worker(scan.id)

    return scan


@router.get("/api/repositories/{repository_id}/scans", response_model=list[ScanSummaryResponse])
async def list_scans(
    repository_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List scan history for a repository."""
    _get_user_repository(db, repository_id, user)
    return (
        db.query(Scan)
        .filter(Scan.repository_id == repository_id)
        .order_by(Scan.created_at.desc())
        .all()
    )


# ── Global scan endpoints ─────────────────────────────────────────────


@router.get("/api/scans/{scan_id}", response_model=ScanResponse)
async def get_scan(
    scan_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch scan detail and status."""
    return _get_user_scan(db, scan_id, user)


@router.delete("/api/scans/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan(
    scan_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a scan and its related files/findings via ORM cascades."""
    scan = _get_user_scan(db, scan_id, user)
    db.delete(scan)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/scans/{scan_id}/files", response_model=list[ScanFileResponse])
async def get_scan_files(
    scan_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch file-level processing results for a scan."""
    _get_user_scan(db, scan_id, user)
    return db.query(ScanFile).filter(ScanFile.scan_id == scan_id).all()


@router.get("/api/scans/{scan_id}/findings", response_model=list[FindingOccurrenceResponse])
async def get_scan_findings(
    scan_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch finding occurrences for a scan, enriched with GitHub deeplinks."""
    scan = _get_user_scan(db, scan_id, user)
    repo = _get_user_repository(db, scan.repository_id, user)
    occurrences = db.query(FindingOccurrence).filter(FindingOccurrence.scan_id == scan_id).all()
    sorted_occurrences = sort_occurrences_by_severity_desc(occurrences)
    return enrich_findings_with_deeplinks(sorted_occurrences, scan, repo)
