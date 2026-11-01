"""Shared fixtures and synthetic documents for the test suite."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas.invoice import ExtractionResult, ExtractionTimings, InvoiceData, LineItem
from app.services.gemini_service import GeminiExtraction

# ---------------------------------------------------------------------------
# Synthetic PDF builders (no real invoices are ever committed)
# ---------------------------------------------------------------------------

INVOICE_LINES = [
    "ACME COMPONENTS PVT LTD",
    "12 Test Lane, Bengaluru, Karnataka 560001",
    "GSTIN: 29ABCDE1234F1Z5",
    "",
    "TAX INVOICE",
    "Invoice number: TEST-2026-001",
    "Invoice date: 15 Mar 2026",
    "Due date: 14 Apr 2026",
    "Bill to: Contoso Retail LLP",
    "123 Example Street, Mumbai, Maharashtra 400001",
    "",
    "Description                 Qty     Rate      Amount",
    "Widget assembly              10   1,500.00   15,000.00",
    "Support plan                  1   5,000.00    5,000.00",
    "",
    "Subtotal: 20,000.00",
    "CGST 9%: 1,800.00",
    "SGST 9%: 1,800.00",
    "Total: 23,600.00 INR",
]


def make_text_pdf(path: Path, lines: list[str] | None = None, pages: int = 1) -> Path:
    """Create a digital PDF with a real text layer."""
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page(width=595, height=842)
        y = 54
        for line in lines or INVOICE_LINES:
            page.insert_text((48, y), line, fontsize=10, fontname="helv")
            y += 18
    document.save(path, garbage=3, deflate=True)
    document.close()
    return path


def make_scanned_pdf(path: Path, lines: list[str] | None = None) -> Path:
    """Create an image-only PDF (no text layer) to exercise OCR."""
    source = fitz.open()
    page = source.new_page(width=595, height=842)
    y = 58
    for line in lines or INVOICE_LINES:
        page.insert_text((48, y), line, fontsize=13, fontname="helv")
        y += 24
    pixmap = page.get_pixmap(dpi=170, alpha=False)
    png = pixmap.tobytes("png")
    source.close()

    scanned = fitz.open()
    image_page = scanned.new_page(width=595, height=842)
    image_page.insert_image(image_page.rect, stream=png)
    scanned.save(path, garbage=3, deflate=True)
    scanned.close()
    return path


def make_encrypted_pdf(path: Path) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Encrypted synthetic invoice", fontsize=12)
    document.save(
        path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    document.close()
    return path


# ---------------------------------------------------------------------------
# Settings / app fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        gemini_api_key="test-key",
        gemini_model="gemini-test",
        gemini_fallback_models_raw="",
        gemini_max_retries=0,
        max_file_size_mb=2,
        max_pages=5,
        max_files_per_request=3,
        max_concurrent_jobs=2,
        job_ttl_hours=1,
        log_level="WARNING",
    )


@pytest.fixture
def digital_pdf(tmp_path: Path) -> Path:
    return make_text_pdf(tmp_path / "digital.pdf")


@pytest.fixture
def scanned_pdf(tmp_path: Path) -> Path:
    return make_scanned_pdf(tmp_path / "scanned.pdf")


@pytest.fixture
def corrupt_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not a valid pdf\n%%EOF\n")
    return path


@pytest.fixture
def encrypted_pdf(tmp_path: Path) -> Path:
    return make_encrypted_pdf(tmp_path / "encrypted.pdf")


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Invoice / result fixtures
# ---------------------------------------------------------------------------


def build_invoice() -> InvoiceData:
    return InvoiceData(
        invoice_number="TEST-2026-001",
        invoice_date=date(2026, 3, 15),
        vendor_name="ACME Components Pvt Ltd",
        vendor_address="12 Test Lane, Bengaluru, Karnataka 560001",
        customer_name="Contoso Retail LLP",
        customer_address="123 Example Street, Mumbai, Maharashtra 400001",
        gst_number="29ABCDE1234F1Z5",
        subtotal=Decimal("20000.00"),
        tax_amount=Decimal("3600.00"),
        total_amount=Decimal("23600.00"),
        currency="INR",
        line_items=[
            LineItem(
                description="Widget assembly",
                quantity=Decimal("10.00"),
                unit_price=Decimal("1500.00"),
                amount=Decimal("15000.00"),
            ),
            LineItem(
                description="Support plan",
                quantity=Decimal("1.00"),
                unit_price=Decimal("5000.00"),
                amount=Decimal("5000.00"),
            ),
        ],
        payment_due_date=date(2026, 4, 14),
    )


def build_result() -> ExtractionResult:
    return ExtractionResult(
        invoice=build_invoice(),
        pdf_type="digital",
        approach="pymupdf+gemini",
        model_used="gemini-test",
        timings=ExtractionTimings(total=1.5, analysis=0.05, text_extraction=0.02, llm=1.4),
        tokens={"prompt": 100, "completion": 50, "total": 150},
        warnings=[],
        raw_text_chars=420,
        extracted_at=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
    )


class StubGemini:
    """Records the call and returns a canned, validated extraction."""

    def __init__(self, invoice: InvoiceData | None = None, error: Exception | None = None):
        self.invoice = invoice or build_invoice()
        self.error = error
        self.calls: list[dict] = []

    async def extract(self, *, text=None, images=None, filename="invoice.pdf"):
        self.calls.append({"text": text, "images": images, "filename": filename})
        if self.error is not None:
            raise self.error
        return GeminiExtraction(
            invoice=self.invoice,
            model_used="stub-model",
            tokens={"prompt": 10, "completion": 20, "total": 30},
            raw_json=self.invoice.model_dump_json(),
        )


@pytest.fixture
def stub_gemini() -> StubGemini:
    return StubGemini()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def upload_files(client: TestClient, paths: list[Path], content_type: str = "application/pdf"):
    opened = []
    try:
        files = []
        for path in paths:
            handle = path.open("rb")
            opened.append(handle)
            files.append(("files", (path.name, handle, content_type)))
        return client.post("/api/v1/invoices", files=files)
    finally:
        for handle in opened:
            handle.close()


def wait_for_job(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/invoices/{job_id}")
        assert response.status_code == 200
        job = response.json()["job"]
        if job["status"] in {"COMPLETED", "FAILED"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish within {timeout}s")
