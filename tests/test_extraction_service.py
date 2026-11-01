"""Tests for the extraction pipeline (analysis -> text/OCR -> validation)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.config import Settings
from app.core.exceptions import ExtractionError, OcrUnavailableError, PdfProcessingError
from app.services.extraction_service import ExtractionService
from app.services.ocr_service import OcrResult
from app.services.pdf_service import PdfService
from conftest import StubGemini, build_invoice

OCR_TEXT = (
    "ACME COMPONENTS PVT LTD\n"
    "Invoice number: TEST-2026-001\n"
    "Invoice date: 15 Mar 2026\n"
    "Total: 23,600.00 INR\n"
)


class FakeOcr:
    def __init__(self, result: OcrResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def extract_pdf(self, path: str, page_count: int) -> OcrResult:
        self.calls.append((path, page_count))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, int, str]] = []

    async def __call__(self, stage: str, progress: int, message: str) -> None:
        self.events.append((stage, progress, message))

    @property
    def stages(self) -> list[str]:
        return [stage for stage, _, _ in self.events]


async def run_pipeline(
    settings: Settings,
    path,
    gemini: StubGemini,
    ocr: FakeOcr | None = None,
) -> tuple[object, Emitter]:
    service = ExtractionService(settings, gemini, PdfService(), ocr or FakeOcr())
    emit = Emitter()
    result = await service.process(
        job_id="test-job",
        file_path=str(path),
        filename=getattr(path, "name", "invoice.pdf"),
        emit=emit,
    )
    return result, emit


@pytest.mark.asyncio
async def test_digital_pdf_uses_text_layer(settings, digital_pdf):
    gemini = StubGemini()

    result, emit = await run_pipeline(settings, digital_pdf, gemini)

    assert result.pdf_type == "digital"
    assert result.approach == "pymupdf+gemini"
    assert result.model_used == "stub-model"
    assert result.tokens == {"prompt": 10, "completion": 20, "total": 30}
    assert result.invoice.invoice_number == "TEST-2026-001"
    assert len(gemini.calls) == 1
    assert "ACME" in gemini.calls[0]["text"]
    assert gemini.calls[0]["images"] is None
    assert emit.stages == [
        "VALIDATING",
        "EXTRACTING_TEXT",
        "CALLING_LLM",
        "VALIDATING_RESULT",
    ]


@pytest.mark.asyncio
async def test_scanned_pdf_uses_ocr_text(settings, scanned_pdf):
    gemini = StubGemini()
    ocr = FakeOcr(result=OcrResult(text=OCR_TEXT, confidence=93.8, pages=1))

    result, emit = await run_pipeline(settings, scanned_pdf, gemini, ocr)

    assert result.pdf_type == "scanned"
    assert result.approach == "tesseract+gemini"
    assert ocr.calls == [(str(scanned_pdf), 1)]
    assert gemini.calls[0]["text"] == OCR_TEXT.strip()
    assert result.warnings == []
    assert "OCR" in emit.stages


@pytest.mark.asyncio
async def test_low_ocr_confidence_produces_warning(settings, scanned_pdf):
    gemini = StubGemini()
    ocr = FakeOcr(result=OcrResult(text=OCR_TEXT, confidence=55.0, pages=1))

    result, _ = await run_pipeline(settings, scanned_pdf, gemini, ocr)

    assert any("OCR confidence is low" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_low_ocr_text_falls_back_to_gemini_vision(settings, scanned_pdf):
    settings = settings.model_copy(update={"ocr_dpi": 72})
    gemini = StubGemini()
    ocr = FakeOcr(result=OcrResult(text="tiny", confidence=0.0, pages=1))

    result, emit = await run_pipeline(settings, scanned_pdf, gemini, ocr)

    assert result.pdf_type == "scanned"
    assert result.approach == "gemini-vision"
    assert gemini.calls[0]["text"] is None
    assert gemini.calls[0]["images"]
    assert any("Gemini Vision" in warning for warning in result.warnings)
    assert "VISION" in emit.stages


@pytest.mark.asyncio
async def test_unavailable_ocr_falls_back_to_vision(settings, scanned_pdf):
    settings = settings.model_copy(update={"ocr_dpi": 72})
    gemini = StubGemini()
    ocr = FakeOcr(error=OcrUnavailableError("Tesseract is missing"))

    result, _ = await run_pipeline(settings, scanned_pdf, gemini, ocr)

    assert result.approach == "gemini-vision"
    assert any("Tesseract unavailable" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_unavailable_ocr_without_vision_raises(settings, scanned_pdf):
    settings = settings.model_copy(update={"enable_gemini_vision_fallback": False})
    gemini = StubGemini()
    ocr = FakeOcr(error=OcrUnavailableError("Tesseract is missing"))

    service = ExtractionService(settings, gemini, PdfService(), ocr)
    with pytest.raises(ExtractionError) as excinfo:
        await service.process(
            job_id="job",
            file_path=str(scanned_pdf),
            filename="scanned.pdf",
            emit=Emitter(),
        )

    assert excinfo.value.code == "OCR_FAILED"


@pytest.mark.asyncio
async def test_generic_ocr_error_falls_back_to_vision(settings, scanned_pdf):
    settings = settings.model_copy(update={"ocr_dpi": 72})
    gemini = StubGemini()
    ocr = FakeOcr(error=RuntimeError("tesseract crashed"))

    result, _ = await run_pipeline(settings, scanned_pdf, gemini, ocr)

    assert result.approach == "gemini-vision"
    assert any("OCR failed" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_invalid_pdf_propagates_error(settings, corrupt_pdf):
    gemini = StubGemini()

    with pytest.raises(PdfProcessingError):
        await run_pipeline(settings, corrupt_pdf, gemini)


@pytest.mark.asyncio
async def test_long_text_is_truncated_with_warning(settings, digital_pdf):
    settings = settings.model_copy(update={"max_text_chars": 30})
    gemini = StubGemini()

    result, _ = await run_pipeline(settings, digital_pdf, gemini)

    assert gemini.calls[0]["text"] is not None
    assert len(gemini.calls[0]["text"]) == 30
    assert result.raw_text_chars == 30
    assert any("truncated" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_validation_flags_inconsistent_values(settings, digital_pdf):
    future = date.today() + timedelta(days=30)
    invoice = build_invoice().model_copy(
        update={
            "invoice_date": future,
            "payment_due_date": future - timedelta(days=1),
            "subtotal": Decimal("10.00"),
            "tax_amount": Decimal("5.00"),
            "total_amount": Decimal("-100.00"),
            "currency": None,
            "gst_number": "NOT-A-GSTIN",
        }
    )
    gemini = StubGemini(invoice=invoice)

    result, _ = await run_pipeline(settings, digital_pdf, gemini)

    joined = " ".join(result.warnings)
    assert "does not match" in joined
    assert "GST number format" in joined
    assert "negative" in joined
    assert "future" in joined
    assert "earlier than" in joined
    assert "Currency could not be determined" in joined


@pytest.mark.asyncio
async def test_validation_accepts_consistent_values(settings, digital_pdf):
    invoice = build_invoice().model_copy(
        update={
            "subtotal": Decimal("100.00"),
            "tax_amount": Decimal("18.00"),
            "total_amount": Decimal("118.00"),
            "currency": "INR",
            "gst_number": "29ABCDE1234F1Z5",
            "invoice_date": date.today() - timedelta(days=2),
            "payment_due_date": date.today() + timedelta(days=10),
        }
    )

    result, _ = await run_pipeline(settings, digital_pdf, StubGemini(invoice=invoice))

    assert result.warnings == []
