"""Invoice upload, listing, download and deletion endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response

from app.api.deps import get_state
from app.config import Settings
from app.core.exceptions import AppError, JobNotFoundError
from app.core.security import save_upload, validate_pdf_structure
from app.schemas.common import UploadResponse, UploadedJob
from app.schemas.job import (
    DeleteResponse,
    JobListResponse,
    JobResponse,
    JobStatus,
)
from app.utils.files import remove_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload one or more PDF invoices",
)
async def upload_invoices(
    request: Request,
    files: list[UploadFile] = File(..., description="One or more PDF invoices"),
) -> UploadResponse:
    state = get_state(request)
    settings: Settings = state.settings

    if not files:
        raise AppError("No files were uploaded.", code="NO_FILES", status_code=400)

    if len(files) > settings.max_files_per_request:
        raise AppError(
            f"Too many files. The limit is {settings.max_files_per_request} per request.",
            code="TOO_MANY_FILES",
            status_code=400,
            details={"received": len(files), "limit": settings.max_files_per_request},
        )

    saved: list = []
    created: list[UploadedJob] = []

    try:
        # Phase 1: persist and structurally validate every file first. This
        # keeps a bad file in a batch from leaving earlier jobs pointing at
        # deleted uploads.
        for upload in files:
            allowed = await save_upload(upload, settings)
            saved.append(allowed)
            await asyncio.to_thread(validate_pdf_structure, allowed.path, settings)

        # Phase 2: only start jobs once the whole batch is known to be valid.
        for allowed in saved:
            job = state.jobs.create_and_start(allowed)
            created.append(
                UploadedJob(job_id=job.id, filename=job.filename, status=job.status.value)
            )

    except Exception:
        # Deletion can block briefly on Windows, so keep it off the event loop.
        for upload in saved:
            await asyncio.to_thread(remove_file, upload.path)
        raise

    logger.info("Accepted %s invoice(s) for processing", len(created))
    return UploadResponse(jobs=created)


@router.get("", response_model=JobListResponse, summary="List extraction jobs")
async def list_invoices(
    request: Request,
    job_status: JobStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> JobListResponse:
    state = get_state(request)
    jobs = state.jobs.list_jobs(status=job_status, limit=limit)
    return JobListResponse(
        total=len(jobs),
        jobs=[job.to_public() for job in jobs],
    )


@router.get("/{job_id}", response_model=JobResponse, summary="Get one job")
async def get_invoice(request: Request, job_id: str) -> JobResponse:
    state = get_state(request)
    return JobResponse(job=state.jobs.get_job(job_id).to_public())


@router.get("/{job_id}/json", summary="Download the extracted JSON")
async def download_json(request: Request, job_id: str) -> Response:
    state = get_state(request)
    job = state.jobs.get_job(job_id)

    if job.result is None:
        raise AppError(
            "The extraction result is not ready yet.",
            code="RESULT_NOT_READY",
            status_code=409,
            details={"status": job.status.value},
        )

    payload = {
        "job_id": job.id,
        "filename": job.filename,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.updated_at.isoformat(),
        "pdf_type": job.result.pdf_type,
        "approach": job.result.approach,
        "model_used": job.result.model_used,
        "result": job.result.model_dump(mode="json"),
    }

    download_name = f"{Path(job.filename).stem or 'invoice'}-extraction.json"
    return Response(
        content=json.dumps(payload, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@router.get("/{job_id}/document", summary="Preview the original PDF")
async def download_document(request: Request, job_id: str) -> FileResponse:
    state = get_state(request)
    job = state.jobs.get_job(job_id)

    if not job.file_path or not Path(job.file_path).is_file():
        raise JobNotFoundError("The original PDF is no longer available.")

    return FileResponse(
        job.file_path,
        media_type="application/pdf",
        filename=job.filename,
        content_disposition_type="inline",
    )


@router.delete("/{job_id}", response_model=DeleteResponse, summary="Delete a job")
async def delete_invoice(request: Request, job_id: str) -> DeleteResponse:
    state = get_state(request)
    state.jobs.delete_job(job_id)
    return DeleteResponse(deleted=job_id)
