"""System endpoints: health and runtime capability reporting."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.api.deps import get_state
from app.schemas.common import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Service health and capabilities")
async def health(request: Request) -> HealthResponse:
    state = get_state(request)
    settings = state.settings

    return HealthResponse(
        status="ok",
        version=settings.app_version,
        gemini_configured=bool(settings.gemini_api_key),
        gemini_model=settings.gemini_model,
        tesseract_available=state.ocr.is_available(),
        tesseract_version=state.ocr.version(),
        max_file_size_mb=settings.max_file_size_mb,
        max_pages=settings.max_pages,
    )
