"""Tesseract OCR with automatic binary discovery and preprocessing."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

from app.config import Settings
from app.core.exceptions import OcrUnavailableError
from app.services.pdf_service import PdfService


def _windows_candidates() -> list[Path]:
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Tesseract-OCR" / "tesseract.exe")
    return candidates


@lru_cache(maxsize=8)
def discover_tesseract(configured: str | None = None) -> str | None:
    """Find the Tesseract executable using config, PATH, then common locations."""
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate)

    on_path = shutil.which("tesseract")
    if on_path:
        return on_path

    for candidate in _windows_candidates():
        if candidate.is_file():
            return str(candidate)

    return None


@lru_cache(maxsize=8)
def tesseract_version(executable: str | None) -> str | None:
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = (result.stdout or result.stderr or "").splitlines()
        return first_line[0].strip() if first_line else None
    except Exception:  # noqa: BLE001 - version probing must never break the app
        return None


@dataclass(slots=True)
class OcrResult:
    text: str
    confidence: float
    pages: int


class OcrService:
    def __init__(self, settings: Settings, pdf_service: PdfService | None = None) -> None:
        self.settings = settings
        self.pdf = pdf_service or PdfService()
        self.executable = discover_tesseract(settings.tesseract_cmd)
        if self.executable:
            pytesseract.pytesseract.tesseract_cmd = self.executable

    def is_available(self) -> bool:
        return self.executable is not None

    def version(self) -> str | None:
        return tesseract_version(self.executable)

    def _require(self) -> str:
        if not self.executable:
            raise OcrUnavailableError(
                "Tesseract OCR is not installed or could not be found. "
                "Install it or set TESSERACT_CMD."
            )
        return self.executable

    @staticmethod
    def preprocess(png_bytes: bytes) -> Image.Image:
        """Convert a page render to a clean grayscale image for OCR."""
        image = Image.open(io.BytesIO(png_bytes)).convert("L")
        image = ImageOps.autocontrast(image)
        return image

    def _image_to_text(self, image: Image.Image) -> tuple[str, float]:
        data = pytesseract.image_to_data(
            image,
            lang=self.settings.tesseract_lang,
            config=f"--oem 3 --psm {self.settings.ocr_psm}",
            output_type=pytesseract.Output.DICT,
        )

        lines: dict[tuple[int, int, int], list[str]] = {}
        confidences: list[float] = []

        for index, raw_text in enumerate(data.get("text", [])):
            text = (raw_text or "").strip()
            if not text:
                continue
            key = (
                data["block_num"][index],
                data["par_num"][index],
                data["line_num"][index],
            )
            lines.setdefault(key, []).append(text)

            try:
                confidence = float(data["conf"][index])
            except (TypeError, ValueError):
                continue
            if confidence >= 0:
                confidences.append(confidence)

        ordered_lines = [
            " ".join(words) for _, words in sorted(lines.items(), key=lambda item: item[0])
        ]
        average_confidence = (
            round(sum(confidences) / len(confidences), 2) if confidences else 0.0
        )
        return "\n".join(ordered_lines), average_confidence

    def extract_pdf(self, path: str, page_count: int) -> OcrResult:
        """OCR every page of a PDF at the configured DPI."""
        self._require()

        texts: list[str] = []
        confidences: list[float] = []

        for page_index in range(page_count):
            png_bytes = self.pdf.render_page_png(path, page_index, self.settings.ocr_dpi)
            image = self.preprocess(png_bytes)
            text, confidence = self._image_to_text(image)
            texts.append(text)
            if confidence:
                confidences.append(confidence)

        combined = "\n\n".join(texts).strip()
        average = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
        return OcrResult(text=combined, confidence=average, pages=page_count)
