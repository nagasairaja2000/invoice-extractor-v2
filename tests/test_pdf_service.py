"""Tests for PyMuPDF analysis and rendering."""

from __future__ import annotations

import pytest

from app.core.exceptions import PdfProcessingError
from app.services.pdf_service import PdfAnalysis, PdfService
from conftest import make_text_pdf

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_analyze_detects_digital_pdf(digital_pdf):
    analysis = PdfService().analyze(str(digital_pdf), max_pages=5)

    assert isinstance(analysis, PdfAnalysis)
    assert analysis.page_count == 1
    assert analysis.is_digital is True
    assert analysis.total_chars > 40
    assert "ACME" in analysis.text_by_page[0]
    assert analysis.warnings == []


def test_analyze_detects_scanned_pdf(scanned_pdf):
    analysis = PdfService().analyze(str(scanned_pdf), max_pages=5)

    assert analysis.page_count == 1
    assert analysis.is_digital is False
    assert analysis.total_chars == 0


def test_analyze_rejects_corrupt_pdf(corrupt_pdf):
    with pytest.raises(PdfProcessingError):
        PdfService().analyze(str(corrupt_pdf))


def test_analyze_rejects_encrypted_pdf(encrypted_pdf):
    with pytest.raises(PdfProcessingError, match="Password-protected"):
        PdfService().analyze(str(encrypted_pdf))


def test_analyze_enforces_page_limit(tmp_path):
    pdf = make_text_pdf(tmp_path / "three-pages.pdf", pages=3)

    with pytest.raises(PdfProcessingError, match="page limit"):
        PdfService().analyze(str(pdf), max_pages=2)


def test_extract_text_marks_page_boundaries(tmp_path):
    pdf = make_text_pdf(tmp_path / "two-pages.pdf", pages=2)
    text = PdfService().extract_text(str(pdf))

    assert "--- Page 1 ---" in text
    assert "--- Page 2 ---" in text
    assert "Invoice number: TEST-2026-001" in text


def test_render_page_png_returns_image(digital_pdf):
    png = PdfService().render_page_png(str(digital_pdf), 0, dpi=72)

    assert png.startswith(PNG_SIGNATURE)
    assert len(png) > 500


def test_render_page_png_rejects_out_of_range_page(digital_pdf):
    with pytest.raises(PdfProcessingError):
        PdfService().render_page_png(str(digital_pdf), 5)


def test_render_all_pages_png(digital_pdf):
    images = PdfService().render_all_pages_png(str(digital_pdf), dpi=72)

    assert len(images) == 1
    assert all(image.startswith(PNG_SIGNATURE) for image in images)
