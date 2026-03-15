"""Scan lifecycle management – status transitions and clone workspace."""

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import git
from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import ScanStatus
from app.models.repository import Repository
from app.models.scan import Scan

logger = logging.getLogger(__name__)


class ScanLifecycleError(Exception):
    """Raised when a scan state transition is invalid."""


def _clone_dir(scan_id: uuid.UUID) -> Path:
    """Return the workspace directory for a scan clone."""
    return Path(settings.clone_base_dir) / str(scan_id)


def transition_to_running(db: Session, scan: Scan) -> None:
    """Mark scan as running. Raises if not in queued state."""
    if scan.status != ScanStatus.queued:
        raise ScanLifecycleError(f"Cannot start scan {scan.id}: expected status 'queued', got '{scan.status}'")
    scan.status = ScanStatus.running
    scan.started_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Scan %s transitioned to running", scan.id)


def transition_to_complete(db: Session, scan: Scan, commit_sha: str | None = None) -> None:
    """Mark scan as complete."""
    scan.status = ScanStatus.complete
    scan.completed_at = datetime.now(timezone.utc)
    if commit_sha:
        scan.commit_sha = commit_sha
    db.commit()
    logger.info("Scan %s transitioned to complete", scan.id)


def transition_to_failed(db: Session, scan: Scan, error_message: str) -> None:
    """Mark scan as failed with an error message."""
    scan.status = ScanStatus.failed
    scan.completed_at = datetime.now(timezone.utc)
    scan.error_message = error_message[:4096]  # Truncate very long errors
    db.commit()
    logger.error("Scan %s failed: %s", scan.id, error_message)


def _detect_default_branch(repo_url: str) -> str:
    """Query the remote to discover the default branch (HEAD symref).

    Falls back to "main" if detection fails.
    """
    try:
        output = git.cmd.Git().ls_remote("--symref", repo_url, "HEAD")
        # First line looks like: ref: refs/heads/master\tHEAD
        for line in output.splitlines():
            if line.startswith("ref: refs/heads/"):
                branch = line.split("refs/heads/")[1].split()[0]
                logger.info("Detected default branch '%s' for %s", branch, repo_url)
                return branch
    except git.GitCommandError as exc:
        logger.warning("Failed to detect default branch for %s: %s", repo_url, exc)
    return "main"


def clone_repository(scan: Scan, repo: Repository) -> tuple[Path, str, str]:
    """Clone the repository into the scan workspace.

    Returns ``(clone_path, branch, commit_sha)``.

    Uses shallow clone (depth=1) for speed. Falls back to default branch if
    none is explicitly set on the repository record.
    """
    clone_path = _clone_dir(scan.id)
    clone_path.mkdir(parents=True, exist_ok=True)

    branch = repo.default_branch or _detect_default_branch(repo.url)
    logger.info("Cloning %s (branch=%s) into %s", repo.url, branch, clone_path)

    cloned = git.Repo.clone_from(
        repo.url,
        str(clone_path),
        branch=branch,
        depth=1,
        single_branch=True,
    )

    commit_sha = cloned.head.commit.hexsha
    logger.info("Cloned %s at commit %s", repo.url, commit_sha)
    return clone_path, branch, commit_sha


def cleanup_workspace(scan_id: uuid.UUID) -> None:
    """Remove the clone workspace for a scan, ignoring errors."""
    clone_path = _clone_dir(scan_id)
    if clone_path.exists():
        shutil.rmtree(clone_path, ignore_errors=True)
        logger.info("Cleaned up workspace for scan %s", scan_id)


def get_scan_with_repo(db: Session, scan_id: uuid.UUID) -> tuple[Scan, Repository]:
    """Fetch a scan and its repository. Raises if not found."""
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise ScanLifecycleError(f"Scan {scan_id} not found")

    repo = db.query(Repository).filter(Repository.id == scan.repository_id).first()
    if not repo:
        raise ScanLifecycleError(f"Repository {scan.repository_id} not found for scan {scan_id}")

    return scan, repo
