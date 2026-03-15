"""File discovery – enumerates candidate .py files and creates scan_file records."""

import logging
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.enums import ProcessingStatus
from app.models.scan_file import ScanFile

logger = logging.getLogger(__name__)

# Directories excluded by default per the PRD.
DEFAULT_EXCLUDED_DIRS: set[str] = {
    "tests",
    ".venv",
    "venv",
    "site-packages",
    "build",
    "dist",
    "__pycache__",
    ".git",
}


def _is_excluded(path: Path, clone_root: Path) -> bool:
    """Return True if *path* falls under any excluded directory."""
    rel = path.relative_to(clone_root)
    for part in rel.parts:
        if part in DEFAULT_EXCLUDED_DIRS:
            return True
    return False


def discover_python_files(clone_path: Path) -> list[str]:
    """Walk *clone_path* and return repo-relative paths for candidate .py files.

    Files in excluded directories are silently skipped.
    Results are sorted for deterministic ordering.
    """
    candidates: list[str] = []
    for py_file in sorted(clone_path.rglob("*.py")):
        if not py_file.is_file():
            continue
        if _is_excluded(py_file, clone_path):
            continue
        rel_path = str(py_file.relative_to(clone_path))
        candidates.append(rel_path)

    logger.info(
        "Discovered %d candidate .py files in %s (excluded dirs: %s)",
        len(candidates),
        clone_path,
        DEFAULT_EXCLUDED_DIRS,
    )
    return candidates


def create_scan_file_records(
    db: Session,
    scan_id: uuid.UUID,
    file_paths: list[str],
) -> list[ScanFile]:
    """Persist a ScanFile record for each discovered file path.

    Returns the list of created ScanFile instances (already flushed so they
    have IDs assigned).
    """
    records: list[ScanFile] = []
    for path in file_paths:
        sf = ScanFile(scan_id=scan_id, file_path=path, processing_status=ProcessingStatus.queued)
        db.add(sf)
        records.append(sf)

    db.flush()  # assign IDs without committing – caller controls the transaction
    logger.info("Created %d scan_file records for scan %s", len(records), scan_id)
    return records
