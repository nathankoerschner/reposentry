"""Scan runner – orchestrates a complete scan job execution."""

import logging
import uuid

from app.database import SessionLocal
from app.models.enums import ScanStatus
from app.scanner.file_discovery import create_scan_file_records, discover_python_files
from app.scanner.pipeline import run_scan_pipeline
from app.services.finding_persistence import persist_findings
from app.services.scan_lifecycle import (
    ScanLifecycleError,
    cleanup_workspace,
    clone_repository,
    get_scan_with_repo,
    transition_to_complete,
    transition_to_failed,
    transition_to_running,
)

logger = logging.getLogger(__name__)


def execute_scan(scan_id: uuid.UUID) -> None:
    """Run a scan job end-to-end.

    This is the top-level entry point called from the Pub/Sub handler.
    It manages its own database session so it can be run in a background thread.

    Phases:
      1. Transition to running
      2. Clone the repository
      3a. Discover candidate .py files (with exclusion rules)
      3b. (Placeholder) LLM scanning pipeline – implemented in Milestone 6
      4. Transition to complete
      5. Cleanup clone workspace
    """
    db = SessionLocal()
    try:
        # ── Load scan ────────────────────────────────────────────
        scan, repo = get_scan_with_repo(db, scan_id)

        # ── Phase 1: running ─────────────────────────────────────
        try:
            transition_to_running(db, scan)
        except ScanLifecycleError as exc:
            logger.warning("Skipping scan %s: %s", scan_id, exc)
            return

        # ── Phase 2: clone ───────────────────────────────────────
        try:
            clone_path, branch, commit_sha = clone_repository(scan, repo)
            if repo.default_branch != branch:
                repo.default_branch = branch
                db.commit()
        except Exception as exc:
            transition_to_failed(db, scan, f"Clone failed: {exc}")
            return

        # ── Phase 3a: file discovery ────────────────────────────
        try:
            candidate_paths = discover_python_files(clone_path)
            scan_files = create_scan_file_records(db, scan.id, candidate_paths)
            db.commit()
            logger.info("Scan %s: discovered %d candidate files", scan_id, len(scan_files))
        except Exception as exc:
            db.rollback()
            transition_to_failed(db, scan, f"File discovery failed: {exc}")
            return

        # ── Phase 3b: LLM scanning pipeline ─────────────────────
        try:
            findings = run_scan_pipeline(db, scan.id, clone_path, scan_files)
            db.commit()
            logger.info("Scan %s: LLM pipeline produced %d findings", scan_id, len(findings))
        except Exception as exc:
            db.rollback()
            transition_to_failed(db, scan, f"LLM scanning failed: {exc}")
            return

        # ── Phase 3c: finding persistence ────────────────────────
        try:
            occurrences = persist_findings(db, scan.id, repo.id, findings)
            db.commit()
            logger.info(
                "Scan %s: persisted %d deduplicated occurrences",
                scan_id,
                len(occurrences),
            )
        except Exception as exc:
            db.rollback()
            transition_to_failed(db, scan, f"Finding persistence failed: {exc}")
            return

        # ── Phase 4: complete ────────────────────────────────────
        transition_to_complete(db, scan, commit_sha=commit_sha)

    except Exception as exc:
        # Catch-all: try to mark the scan as failed
        logger.exception("Unexpected error in scan %s", scan_id)
        try:
            db.rollback()
            scan, _ = get_scan_with_repo(db, scan_id)
            if scan.status == ScanStatus.running:
                transition_to_failed(db, scan, f"Unexpected error: {exc}")
        except Exception:
            logger.exception("Failed to mark scan %s as failed", scan_id)
    finally:
        # ── Phase 5: cleanup ─────────────────────────────────────
        cleanup_workspace(scan_id)
        db.close()
