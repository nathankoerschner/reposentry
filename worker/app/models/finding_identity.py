"""FindingIdentity model – worker-side mirror for cross-scan dedup."""

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FindingIdentity(Base):
    __tablename__ = "finding_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_vulnerability_type: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.now(), nullable=False)

    # Relationships
    repository = relationship("Repository", back_populates="finding_identities")
    occurrences = relationship("FindingOccurrence", back_populates="finding_identity")

    __table_args__ = (UniqueConstraint("repository_id", "fingerprint", name="uq_finding_identity_repo_fingerprint"),)
