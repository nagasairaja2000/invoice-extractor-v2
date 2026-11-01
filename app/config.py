"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed application settings.

    Values are read from environment variables first, then from `.env`.
    Unknown variables are ignored so the app can share a host environment.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "Invoice Extractor AI"
    app_version: str = "2.0.0"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Gemini
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_fallback_models_raw: str = "gemini-3.5-flash-lite,gemini-3.8-flash"
    gemini_timeout_seconds: int = 120
    gemini_max_retries: int = 2
    gemini_max_output_tokens: int = 4096
    enable_gemini_vision_fallback: bool = True

    # Upload / processing limits
    max_file_size_mb: int = 15
    max_pages: int = 20
    max_files_per_request: int = 10
    max_concurrent_jobs: int = 3
    job_ttl_hours: int = 24
    max_text_chars: int = 60_000

    # OCR
    tesseract_cmd: str | None = None
    tesseract_lang: str = "eng"
    ocr_dpi: int = 300
    ocr_min_chars: int = 40
    ocr_psm: int = 6

    # Storage / server
    data_dir: Path = PROJECT_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8001

    @field_validator("data_dir", mode="after")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value)

    @field_validator("tesseract_cmd", mode="before")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @property
    def gemini_fallback_models(self) -> list[str]:
        return [m.strip() for m in self.gemini_fallback_models_raw.split(",") if m.strip()]

    @property
    def gemini_models(self) -> list[str]:
        """Primary model first, then fallbacks, de-duplicated."""
        return list(dict.fromkeys([self.gemini_model, *self.gemini_fallback_models]))

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
