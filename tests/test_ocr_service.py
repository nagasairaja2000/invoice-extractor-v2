"""Tests for Tesseract discovery, preprocessing and OCR text assembly."""

from __future__ import annotations

import io

import pytest
from PIL import Image

import app.services.ocr_service as ocr_module
from app.core.exceptions import OcrUnavailableError
from app.services.ocr_service import (
    OcrResult,
    OcrService,
    discover_tesseract,
    tesseract_version,
)


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    discover_tesseract.cache_clear()
    tesseract_version.cache_clear()
    yield
    discover_tesseract.cache_clear()
    tesseract_version.cache_clear()


def test_discover_tesseract_prefers_configured_path(tmp_path, monkeypatch):
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"not really a binary")
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: "C:/wrong/tesseract.exe")

    assert discover_tesseract(str(executable)) == str(executable)


def test_discover_tesseract_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(ocr_module, "_windows_candidates", lambda: [])
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: "C:/toasts/tesseract.exe")

    assert discover_tesseract() == "C:/toasts/tesseract.exe"


def test_discover_tesseract_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(ocr_module, "_windows_candidates", lambda: [])
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: None)

    assert discover_tesseract() is None
    assert discover_tesseract("C:/does/not/exist.exe") is None


def test_tesseract_version_handles_missing_executable():
    assert tesseract_version(None) is None


def test_service_reports_unavailable(settings, monkeypatch):
    monkeypatch.setattr(ocr_module, "discover_tesseract", lambda *_args, **_kwargs: None)
    service = OcrService(settings)

    assert service.is_available() is False
    assert service.version() is None
    with pytest.raises(OcrUnavailableError):
        service.extract_pdf("whatever.pdf", 1)


def test_preprocess_returns_grayscale_image():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 32), (10, 120, 220)).save(buffer, format="PNG")

    image = OcrService.preprocess(buffer.getvalue())

    assert image.mode == "L"
    assert image.size == (64, 32)


def test_image_to_text_groups_words_into_lines(settings, monkeypatch):
    service = OcrService(settings)
    payload = {
        "text": ["Hello", "", "World", "Invoice", "Total", "junk"],
        "conf": ["95", "-1", "85", "90", "70", "not-a-number"],
        "block_num": [1, 1, 1, 1, 1, 1],
        "par_num": [0, 0, 0, 0, 0, 0],
        "line_num": [0, 0, 0, 1, 1, 1],
    }
    monkeypatch.setattr(ocr_module.pytesseract, "image_to_data", lambda *a, **k: payload)

    text, confidence = service._image_to_text(Image.new("L", (10, 10)))

    assert text == "Hello World\nInvoice Total junk"
    assert confidence == pytest.approx(85.0)


def test_extract_pdf_combines_pages(settings, monkeypatch):
    service = OcrService(settings)
    service.executable = "tesseract"  # bypass discovery; OCR calls are stubbed

    class FakePdf:
        def render_page_png(self, path, index, dpi):
            buffer = io.BytesIO()
            Image.new("L", (20, 20), 255).save(buffer, format="PNG")
            return buffer.getvalue()

    service.pdf = FakePdf()
    monkeypatch.setattr(service, "_image_to_text", lambda _image: ("page words", 80.0))

    result = service.extract_pdf("invoice.pdf", 2)

    assert isinstance(result, OcrResult)
    assert result.pages == 2
    assert result.text == "page words\n\npage words"
    assert result.confidence == 80.0


@pytest.mark.skipif(
    discover_tesseract() is None, reason="Tesseract is not installed on this machine"
)
def test_real_tesseract_reads_synthetic_scan(settings, scanned_pdf):
    settings = settings.model_copy(update={"ocr_dpi": 150})
    service = OcrService(settings)

    result = service.extract_pdf(str(scanned_pdf), 1)

    upper = result.text.upper()
    assert "INVOICE" in upper or "ACME" in upper
    assert result.confidence > 0
    assert result.pages == 1
