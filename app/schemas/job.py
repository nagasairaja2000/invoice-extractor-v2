"""Job lifecycle schemas shared by REST and WebSocket APIs."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ErrorInfo
from app.schemas.invoice import ExtractionResult


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.COMPLETED, JobStatus.FAILED}


class JobEventType(str, Enum):
    SNAPSHOT = "snapshot"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"


class JobEvent(BaseModel):
    type: JobEventType
    job_id: str
    status: JobStatus
    stage: str | None = None
    progress: int = 0
    message: str = ""
    data: dict[str, Any] | None = None


class Job(BaseModel):
    """Internal job representation (includes the server-side file path)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    filename: str
    status: JobStatus = JobStatus.QUEUED
    stage: str | None = None
    progress: int = 0
    message: str = "Queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pdf_type: str | None = None
    approach: str | None = None
    result: ExtractionResult | None = None
    error: ErrorInfo | None = None
    file_path: str | None = Field(default=None, exclude=True)

    def to_public(self) -> "JobPublic":
        return JobPublic(
            id=self.id,
            filename=self.filename,
            status=self.status,
            stage=self.stage,
            progress=self.progress,
            message=self.message,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pdf_type=self.pdf_type,
            approach=self.approach,
            result=self.result,
            error=self.error,
        )


class JobPublic(BaseModel):
    id: str
    filename: str
    status: JobStatus
    stage: str | None = None
    progress: int = 0
    message: str = ""
    created_at: datetime
    updated_at: datetime
    pdf_type: str | None = None
    approach: str | None = None
    result: ExtractionResult | None = None
    error: ErrorInfo | None = None


class JobListResponse(BaseModel):
    success: bool = True
    total: int
    jobs: list[JobPublic]


class JobResponse(BaseModel):
    success: bool = True
    job: JobPublic


class DeleteResponse(BaseModel):
    success: bool = True
    deleted: str
