"""Finding persistence – fingerprint, deduplicate, and store scan findings.

Takes the raw FindingResult list from the LLM pipeline and:
1. Generates a fingerprint per finding
2. Deduplicates within the scan (same fingerprint → keep first)
3. Upserts FindingIdentity records (get-or-create by repo + fingerprint)
4. Creates FindingOccurrence records linked to identities
5. Creates default FindingTriage records (status=open)

The caller is responsible for committing the session.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.models.enums import Severity, TriageStatus
from app.models.finding_identity import FindingIdentity
from app.models.finding_occurrence import FindingOccurrence
from app.models.finding_triage import FindingTriage
from app.scanner.pipeline import FindingResult
from app.services.fingerprint import generate_fingerprint

logger = logging.getLogger(__name__)


def _get_or_create_identity(
    db: Session,
    repository_id: uuid.UUID,
    fingerprint: str,
    canonical_vulnerability_type: str,
    canonical_file_path: str,
) -> FindingIdentity:
    """Return an existing FindingIdentity or create a new one."""
    identity = (
        db.query(FindingIdentity)
        .filter(
            FindingIdentity.repository_id == repository_id,
            FindingIdentity.fingerprint == fingerprint,
        )
        .first()
    )
    if identity is not None:
        return identity

    identity = FindingIdentity(
        repository_id=repository_id,
        fingerprint=fingerprint,
        canonical_vulnerability_type=canonical_vulnerability_type,
        canonical_file_path=canonical_file_path,
    )
    db.add(identity)
    db.flush()  # assign id
    return identity


def persist_findings(
    db: Session,
    scan_id: uuid.UUID,
    repository_id: uuid.UUID,
    findings: list[FindingResult],
) -> list[FindingOccurrence]:
    """Deduplicate and persist a batch of scan findings.

    Returns the list of created FindingOccurrence objects.
    The session is flushed but NOT committed – the caller owns the transaction.
    """
    if not findings:
        return []

    # ── Step 1 & 2: fingerprint and deduplicate within the scan ──
    seen_fingerprints: dict[str, FindingResult] = {}
    for f in findings:
        fp = generate_fingerprint(f.file_path, f.vulnerability_type)
        if fp not in seen_fingerprints:
            seen_fingerprints[fp] = f

    dedup_count = len(findings) - len(seen_fingerprints)
    if dedup_count > 0:
        logger.info(
            "Scan %s: deduplicated %d findings (%d → %d)",
            scan_id,
            dedup_count,
            len(findings),
            len(seen_fingerprints),
        )

    # ── Step 3–5: upsert identities, create occurrences + triage ──
    occurrences: list[FindingOccurrence] = []
    for fingerprint, finding in seen_fingerprints.items():
        identity = _get_or_create_identity(
            db,
            repository_id=repository_id,
            fingerprint=fingerprint,
            canonical_vulnerability_type=finding.vulnerability_type,
            canonical_file_path=finding.file_path,
        )

        occurrence = FindingOccurrence(
            scan_id=scan_id,
            finding_identity_id=identity.id,
            file_path=finding.file_path,
            line_number=finding.line_number,
            severity=Severity(finding.severity),
            vulnerability_type=finding.vulnerability_type,
            description=finding.description,
            explanation=finding.explanation,
            code_snippet=finding.code_snippet,
        )
        db.add(occurrence)
        db.flush()  # assign occurrence.id

        triage = FindingTriage(
            finding_occurrence_id=occurrence.id,
            status=TriageStatus.open,
        )
        db.add(triage)

        occurrences.append(occurrence)

    db.flush()
    logger.info(
        "Scan %s: persisted %d finding occurrences (%d identities touched)",
        scan_id,
        len(occurrences),
        len(seen_fingerprints),
    )
    return occurrences
