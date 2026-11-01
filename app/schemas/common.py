"""Shared API response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorInfo
    request_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    gemini_configured: bool
    gemini_model: str
    tesseract_available: bool
    tesseract_version: str | None = None
    max_file_size_mb: int
    max_pages: int


class UploadedJob(BaseModel):
    job_id: str
    filename: str
    status: str


class UploadResponse(BaseModel):
    success: bool = True
    jobs: list[UploadedJob] = Field(default_factory=list)
