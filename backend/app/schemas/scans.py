"""Scan request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ProcessingStatus, ScanStatus, Stage1Result


class ScanCreate(BaseModel):
    """Body for POST /repositories/{id}/scans – currently empty, reserved for future options."""

    pass


class ScanSummaryResponse(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    status: ScanStatus
    commit_sha: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScanResponse(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    status: ScanStatus
    commit_sha: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScanFileResponse(BaseModel):
    id: uuid.UUID
    scan_id: uuid.UUID
    file_path: str
    stage1_result: Stage1Result | None
    stage2_attempted: bool
    processing_status: ProcessingStatus | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


class ActiveScanFileResponse(BaseModel):
    id: uuid.UUID
    file_path: str
    stage1_result: Stage1Result | None
    processing_status: ProcessingStatus
    started_at: datetime | None

    model_config = {"from_attributes": True}


class ScanProgressResponse(BaseModel):
    status: ScanStatus
    files_total: int
    files_queued: int
    files_running: int
    files_complete: int
    files_failed: int
    files_skipped: int
    findings_so_far: int
    active_files: list[ActiveScanFileResponse]
