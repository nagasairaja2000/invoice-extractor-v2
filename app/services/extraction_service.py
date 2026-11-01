"""Extraction orchestration: PDF -> text/OCR -> Gemini -> validation."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal
from time import monotonic
from typing import Awaitable, Callable

from app.config import Settings
from app.core.exceptions import ExtractionError, OcrUnavailableError
from app.schemas.invoice import (
    GSTIN_PATTERN,
    ExtractionResult,
    ExtractionTimings,
    InvoiceData,
)
from app.services.gemini_service import GeminiService
from app.services.ocr_service import OcrService
from app.services.pdf_service import PdfService
from app.utils.text import clean_text

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, int, str], Awaitable[None]]


class ExtractionService:
    def __init__(
        self,
        settings: Settings,
        gemini_service: GeminiService,
        pdf_service: PdfService | None = None,
        ocr_service: OcrService | None = None,
    ) -> None:
        self.settings = settings
        self.gemini = gemini_service
        self.pdf = pdf_service or PdfService()
        self.ocr = ocr_service or OcrService(settings, self.pdf)

    async def process(
        self,
        *,
        job_id: str,
        file_path: str,
        filename: str,
        emit: EmitFn,
    ) -> ExtractionResult:
        started = monotonic()
        timings = ExtractionTimings()
        warnings: list[str] = []

        await emit("VALIDATING", 5, "Validating PDF structure")

        step_started = monotonic()
        analysis = await asyncio.to_thread(self.pdf.analyze, file_path, self.settings.max_pages)
        timings.analysis = round(monotonic() - step_started, 3)
        warnings.extend(analysis.warnings)

        text: str | None = None
        images: list[bytes] | None = None
        approach: str
        pdf_type: str

        if analysis.is_digital:
            pdf_type = "digital"
            approach = "pymupdf+gemini"

            await emit("EXTRACTING_TEXT", 25, "Extracting selectable text with PyMuPDF")
            step_started = monotonic()
            text = await asyncio.to_thread(self.pdf.extract_text, file_path)
            timings.text_extraction = round(monotonic() - step_started, 3)
        else:
            pdf_type = "scanned"
            approach = "tesseract+gemini"

            await emit(
                "OCR",
                20,
                f"Running Tesseract OCR on {analysis.page_count} page(s)",
            )
            step_started = monotonic()
            threshold = self.settings.ocr_min_chars * max(analysis.page_count, 1)

            try:
                ocr_result = await asyncio.to_thread(
                    self.ocr.extract_pdf, file_path, analysis.page_count
                )
                timings.ocr = round(monotonic() - step_started, 3)

                if len(ocr_result.text.strip()) >= threshold:
                    text = ocr_result.text
                    if ocr_result.confidence and ocr_result.confidence < 60:
                        warnings.append(
                            f"OCR confidence is low ({ocr_result.confidence}%). "
                            "Review the extracted values."
                        )
                else:
                    warnings.append("OCR produced very little text.")
            except OcrUnavailableError as exc:
                timings.ocr = round(monotonic() - step_started, 3)
                warnings.append(f"Tesseract unavailable: {exc.message}")
            except Exception as exc:  # noqa: BLE001 - OCR must not crash the job
                timings.ocr = round(monotonic() - step_started, 3)
                logger.exception("OCR failed for job %s", job_id)
                warnings.append(f"OCR failed: {exc}")

            if not text:
                if not self.settings.enable_gemini_vision_fallback:
                    raise ExtractionError(
                        "OCR did not produce usable text and the Gemini Vision "
                        "fallback is disabled.",
                        code="OCR_FAILED",
                    )

                await emit(
                    "VISION",
                    40,
                    "Rendering pages for the Gemini Vision fallback",
                )
                step_started = monotonic()
                images = await asyncio.to_thread(
                    self.pdf.render_all_pages_png, file_path, self.settings.ocr_dpi
                )
                timings.vision = round(monotonic() - step_started, 3)
                approach = "gemini-vision"
                warnings.append("Used Gemini Vision because the OCR text was unusable.")

        raw_text_chars = len(text) if text else 0

        if text:
            text, truncated = clean_text(text, self.settings.max_text_chars)
            raw_text_chars = len(text)
            if truncated:
                warnings.append(
                    f"Invoice text was truncated to {self.settings.max_text_chars} characters."
                )

        await emit("CALLING_LLM", 65, "Extracting structured fields with Gemini")

        step_started = monotonic()
        extraction = await self.gemini.extract(
            text=text,
            images=images,
            filename=filename,
        )
        timings.llm = round(monotonic() - step_started, 3)

        await emit("VALIDATING_RESULT", 90, "Validating and normalising extracted values")
        warnings.extend(self._validate_invoice(extraction.invoice))

        timings.total = round(monotonic() - started, 3)

        return ExtractionResult(
            invoice=extraction.invoice,
            pdf_type=pdf_type,  # type: ignore[arg-type]
            approach=approach,
            model_used=extraction.model_used,
            timings=timings,
            tokens=extraction.tokens,
            warnings=warnings,
            raw_text_chars=raw_text_chars,
        )

    @staticmethod
    def _validate_invoice(invoice: InvoiceData) -> list[str]:
        warnings: list[str] = []

        if (
            invoice.subtotal is not None
            and invoice.tax_amount is not None
            and invoice.total_amount is not None
        ):
            expected = invoice.subtotal + invoice.tax_amount
            if abs(expected - invoice.total_amount) > Decimal("0.05"):
                warnings.append(
                    "Subtotal plus tax does not match the total "
                    f"({expected} vs {invoice.total_amount})."
                )

        if invoice.gst_number and not GSTIN_PATTERN.match(invoice.gst_number):
            warnings.append("GST number format looks invalid.")

        if invoice.total_amount is not None and invoice.total_amount < 0:
            warnings.append("Total amount is negative.")

        if invoice.invoice_date and invoice.invoice_date > date.today() + timedelta(days=1):
            warnings.append("Invoice date is in the future.")

        if (
            invoice.payment_due_date
            and invoice.invoice_date
            and invoice.payment_due_date < invoice.invoice_date
        ):
            warnings.append("Payment due date is earlier than the invoice date.")

        if invoice.total_amount is not None and not invoice.currency:
            warnings.append("Currency could not be determined.")

        return warnings
