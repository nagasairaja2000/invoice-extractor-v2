"""FastAPI application factory and entrypoint."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.deps import AppState
from app.api.routes import invoices, pages, system, ws
from app.config import PROJECT_ROOT, Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.services.extraction_service import ExtractionService
from app.services.gemini_service import GeminiService
from app.services.job_manager import JobManager
from app.services.ocr_service import OcrService
from app.services.pdf_service import PdfService
from app.services.storage import Storage

logger = logging.getLogger(__name__)

STATIC_DIR = PROJECT_ROOT / "app" / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage = Storage(settings)
        pdf_service = PdfService()
        ocr_service = OcrService(settings, pdf_service)
        gemini_service = GeminiService(settings)
        extraction_service = ExtractionService(
            settings, gemini_service, pdf_service, ocr_service
        )
        job_manager = JobManager(settings, extraction_service, storage)

        app.state.services = AppState(
            settings=settings,
            storage=storage,
            ocr=ocr_service,
            gemini=gemini_service,
            extraction=extraction_service,
            jobs=job_manager,
        )

        await job_manager.start()
        logger.info(
            "%s v%s ready on http://%s:%s (gemini=%s, tesseract=%s)",
            settings.app_name,
            settings.app_version,
            settings.host,
            settings.port,
            "configured" if settings.gemini_api_key else "missing key",
            ocr_service.version() or "unavailable",
        )
        try:
            yield
        finally:
            await job_manager.shutdown()
            logger.info("Shutdown complete")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Extract structured invoice data from digital and scanned PDFs using "
            "PyMuPDF, Tesseract OCR and Google Gemini."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    register_exception_handlers(app)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(pages.router)
    app.include_router(system.router)
    app.include_router(invoices.router)
    app.include_router(ws.router)

    return app


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", "8001"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("app.main:app", host=host, port=port)

