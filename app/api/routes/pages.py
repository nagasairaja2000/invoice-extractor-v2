"""Server-rendered dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import get_state
from app.config import PROJECT_ROOT

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request) -> HTMLResponse:
    state = get_state(request)
    settings = state.settings

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "gemini_configured": bool(settings.gemini_api_key),
            "gemini_model": settings.gemini_model,
            "tesseract_available": state.ocr.is_available(),
            "tesseract_version": state.ocr.version(),
            "max_file_size_mb": settings.max_file_size_mb,
            "max_pages": settings.max_pages,
            "max_files": settings.max_files_per_request,
            "job_ttl_hours": settings.job_ttl_hours,
        },
    )


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> RedirectResponse:
    return RedirectResponse(url="/static/img/favicon.svg", status_code=307)
