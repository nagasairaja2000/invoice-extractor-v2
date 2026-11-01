"""Persistence for extracted JSON artifacts (also powers history)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings
from app.schemas.invoice import ExtractionResult

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_dirs()

    def artifact_path(self, job_id: str) -> Path:
        return self.settings.output_dir / f"{job_id}.json"

    def save_result(
        self,
        *,
        job_id: str,
        filename: str,
        created_at: datetime,
        completed_at: datetime,
        result: ExtractionResult,
    ) -> Path:
        path = self.artifact_path(job_id)
        payload: dict[str, Any] = {
            "job_id": job_id,
            "filename": filename,
            "created_at": created_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "pdf_type": result.pdf_type,
            "approach": result.approach,
            "model_used": result.model_used,
            "result": result.model_dump(mode="json"),
        }

        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def load_result(self, job_id: str) -> dict[str, Any] | None:
        path = self.artifact_path(job_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read artifact %s", path, exc_info=True)
            return None

    def iter_artifacts(self) -> Iterator[dict[str, Any]]:
        for path in sorted(
            self.settings.output_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                yield json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("Skipping unreadable artifact %s", path)

    def delete_result(self, job_id: str) -> bool:
        path = self.artifact_path(job_id)
        if path.is_file():
            path.unlink(missing_ok=True)
            return True
        return False
