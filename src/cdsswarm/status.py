"""Status enums for worker and file lifecycle states."""

from __future__ import annotations

import enum


class WorkerStatus(str, enum.Enum):
    """CDS worker lifecycle status."""

    IDLE = "idle"
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


class FileStatus(str, enum.Enum):
    """UI file tracking status."""

    PENDING = "pending"
    CACHED = "cached"
    ACTIVE = "active"
    SUCCESSFUL = "successful"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value
