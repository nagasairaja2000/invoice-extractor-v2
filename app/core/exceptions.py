"""Application exception hierarchy and FastAPI exception handlers."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, user-facing application errors."""

    status_code: int = 400
    code: str = "APP_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: Any | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class InvalidPdfError(AppError):
    status_code = 422
    code = "INVALID_PDF"


class PdfProcessingError(AppError):
    status_code = 422
    code = "PDF_PROCESSING_FAILED"


class UnsupportedFileTypeError(AppError):
    status_code = 415
    code = "UNSUPPORTED_FILE_TYPE"


class FileTooLargeError(AppError):
    status_code = 413
    code = "FILE_TOO_LARGE"


class OcrUnavailableError(AppError):
    status_code = 503
    code = "OCR_UNAVAILABLE"


class ExtractionError(AppError):
    status_code = 502
    code = "EXTRACTION_FAILED"


class JobNotFoundError(AppError):
    status_code = 404
    code = "JOB_NOT_FOUND"


def error_payload(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details},
    }


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("Application error: %s (%s)", exc.message, exc.code, exc_info=exc)
        else:
            logger.info("Request rejected: %s (%s)", exc.message, exc.code)

        payload = error_payload(exc.code, exc.message, exc.details)
        payload["request_id"] = _request_id(request)
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error.get("loc", [])),
                "message": error.get("msg", "Invalid value"),
            }
            for error in exc.errors()
        ]
        payload = error_payload("VALIDATION_ERROR", "Request validation failed.", details)
        payload["request_id"] = _request_id(request)
        return JSONResponse(status_code=422, content=payload)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        payload = error_payload(code, str(exc.detail))
        payload["request_id"] = _request_id(request)
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception while processing %s", request.url.path)
        payload = error_payload(
            "INTERNAL_ERROR",
            "An unexpected error occurred. Please try again.",
        )
        payload["request_id"] = _request_id(request)
        return JSONResponse(status_code=500, content=payload)
