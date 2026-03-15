"""ScanFile model – file-level processing results for a scan."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import ProcessingStatus, Stage1Result


class ScanFile(Base):
    __tablename__ = "scan_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scans.id"), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    stage1_result: Mapped[Stage1Result | None] = mapped_column(nullable=True)
    stage2_attempted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processing_status: Mapped[ProcessingStatus | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    scan = relationship("Scan", back_populates="scan_files")
