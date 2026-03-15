"""Shared enumerations (mirrors backend/app/models/enums.py)."""

import enum


class ScanStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"


class Stage1Result(str, enum.Enum):
    suspicious = "suspicious"
    not_suspicious = "not_suspicious"
    failed = "failed"


class ProcessingStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"
    skipped = "skipped"


class Severity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class TriageStatus(str, enum.Enum):
    open = "open"
    false_positive = "false_positive"
    resolved = "resolved"
