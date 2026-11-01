"""Dependency wiring for REST and WebSocket routes."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, WebSocket

from app.config import Settings
from app.services.extraction_service import ExtractionService
from app.services.gemini_service import GeminiService
from app.services.job_manager import JobManager
from app.services.ocr_service import OcrService
from app.services.storage import Storage


@dataclass(slots=True)
class AppState:
    """Container for long-lived services, stored on ``app.state``."""

    settings: Settings
    storage: Storage
    ocr: OcrService
    gemini: GeminiService
    extraction: ExtractionService
    jobs: JobManager


def get_state(request: Request) -> AppState:
    return request.app.state.services


def get_websocket_state(websocket: WebSocket) -> AppState:
    return websocket.app.state.services


def get_settings_from_state(request: Request) -> Settings:
    return get_state(request).settings


def get_job_manager(request: Request) -> JobManager:
    return get_state(request).jobs


def get_storage(request: Request) -> Storage:
    return get_state(request).storage
