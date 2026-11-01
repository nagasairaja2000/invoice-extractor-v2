"""End-to-end HTTP tests for the invoice API."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import ExtractionError
from conftest import StubGemini, upload_files, wait_for_job


@pytest.fixture
def stub(client) -> StubGemini:
    """Replace Gemini inside the running app with a deterministic stub."""
    stub = StubGemini()
    client.app.state.services.jobs.extraction.gemini = stub
    return stub


def test_health_reports_capabilities(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == "2.0.0"
    assert payload["gemini_configured"] is True
    assert payload["gemini_model"] == "gemini-test"
    assert isinstance(payload["tesseract_available"], bool)
    assert payload["max_file_size_mb"] == 2
    assert payload["max_pages"] == 5


def test_dashboard_renders(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Invoice Extractor" in response.text
    assert 'id="dropzone"' in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert len(response.headers["x-request-id"]) == 12


def test_favicon_redirects_to_static_asset(client):
    response = client.get("/favicon.ico", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/static/img/favicon.svg"


def test_openapi_and_docs_are_available(client):
    docs = client.get("/api/docs")
    schema = client.get("/api/openapi.json")

    assert docs.status_code == 200
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/v1/invoices" in paths
    assert "/api/v1/health" in paths


def test_upload_and_extract_digital_invoice(client, stub, digital_pdf):
    response = upload_files(client, [digital_pdf])

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert len(body["jobs"]) == 1
    job_id = body["jobs"][0]["job_id"]
    assert body["jobs"][0]["status"] == "QUEUED"

    job = wait_for_job(client, job_id)

    assert job["status"] == "COMPLETED"
    assert job["progress"] == 100
    assert job["pdf_type"] == "digital"
    assert job["approach"] == "pymupdf+gemini"
    assert job["result"]["invoice"]["invoice_number"] == "TEST-2026-001"
    assert job["result"]["invoice"]["currency"] == "INR"
    assert "TEST-2026-001" in stub.calls[0]["text"]


def test_upload_accepts_multiple_files(client, stub, digital_pdf, scanned_pdf):
    response = upload_files(client, [digital_pdf, scanned_pdf], content_type="application/pdf")

    assert response.status_code == 202
    jobs = response.json()["jobs"]
    assert len(jobs) == 2

    for entry in jobs:
        job = wait_for_job(client, entry["job_id"])
        assert job["status"] == "COMPLETED"


def test_upload_rejects_too_many_files(client, digital_pdf):
    response = upload_files(client, [digital_pdf] * 4)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TOO_MANY_FILES"


def test_upload_rejects_non_pdf_extension(client):
    response = client.post(
        "/api/v1/invoices",
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_upload_rejects_wrong_content_type(client, digital_pdf):
    response = client.post(
        "/api/v1/invoices",
        files=[("files", (digital_pdf.name, digital_pdf.read_bytes(), "image/png"))],
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_upload_rejects_empty_file(client):
    response = client.post(
        "/api/v1/invoices",
        files=[("files", ("empty.pdf", b"", "application/pdf"))],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PDF"


def test_upload_rejects_file_without_pdf_signature(client):
    response = client.post(
        "/api/v1/invoices",
        files=[("files", ("fake.pdf", b"just some text", "application/pdf"))],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PDF"


def test_upload_rejects_corrupt_pdf(client, corrupt_pdf):
    response = upload_files(client, [corrupt_pdf])

    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"INVALID_PDF", "PDF_PROCESSING_FAILED"}


def test_upload_rejects_oversized_file(client):
    settings = client.app.state.services.settings
    payload = b"%PDF-1.4\n" + b"0" * (settings.max_file_size_bytes + 10)
    response = client.post(
        "/api/v1/invoices",
        files=[("files", ("huge.pdf", payload, "application/pdf"))],
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_partial_batch_failure_leaves_no_uploads_or_jobs(client, settings, digital_pdf, corrupt_pdf):
    before = client.app.state.services.jobs.list_jobs()

    response = upload_files(client, [digital_pdf, corrupt_pdf])

    assert response.status_code == 422
    assert list(settings.upload_dir.glob("*.pdf")) == []
    assert client.app.state.services.jobs.list_jobs() == before


def test_upload_requires_a_file(client):
    response = client.post("/api/v1/invoices")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_unknown_job_returns_structured_404(client):
    response = client.get("/api/v1/invoices/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "JOB_NOT_FOUND"
    assert body["request_id"]


def test_list_jobs_filters_and_limits(client, stub, digital_pdf):
    job_id = upload_files(client, [digital_pdf]).json()["jobs"][0]["job_id"]
    wait_for_job(client, job_id)

    completed = client.get("/api/v1/invoices", params={"status": "COMPLETED"})
    failed = client.get("/api/v1/invoices", params={"status": "FAILED"})
    limited = client.get("/api/v1/invoices", params={"limit": 1})

    assert completed.status_code == 200
    assert [job["id"] for job in completed.json()["jobs"]] == [job_id]
    assert failed.json()["total"] == 0
    assert len(limited.json()["jobs"]) == 1


def test_list_jobs_rejects_invalid_status(client):
    response = client.get("/api/v1/invoices", params={"status": "NOPE"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_download_json_after_completion(client, stub, digital_pdf):
    job_id = upload_files(client, [digital_pdf]).json()["jobs"][0]["job_id"]
    wait_for_job(client, job_id)

    response = client.get(f"/api/v1/invoices/{job_id}/json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["job_id"] == job_id
    assert payload["filename"] == "digital.pdf"
    assert payload["result"]["invoice"]["vendor_name"] == "ACME Components Pvt Ltd"
    assert payload["result"]["timings"]["total"] > 0


def test_download_json_requires_result(client):
    response = client.get("/api/v1/invoices/missing/json")

    assert response.status_code == 404


def test_download_document_and_delete(client, stub, settings, digital_pdf):
    job_id = upload_files(client, [digital_pdf]).json()["jobs"][0]["job_id"]
    wait_for_job(client, job_id)

    manager = client.app.state.services.jobs
    upload_path = Path(manager.get_job(job_id).file_path)

    document = client.get(f"/api/v1/invoices/{job_id}/document")
    assert document.status_code == 200
    assert document.headers["content-type"] == "application/pdf"
    assert document.content.startswith(b"%PDF-")

    deleted = client.delete(f"/api/v1/invoices/{job_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"success": True, "deleted": job_id}
    assert not upload_path.exists()
    assert manager.storage.load_result(job_id) is None

    assert client.get(f"/api/v1/invoices/{job_id}").status_code == 404
    assert client.get(f"/api/v1/invoices/{job_id}/document").status_code == 404
    assert client.get(f"/api/v1/invoices/{job_id}/json").status_code == 404


def test_failed_extraction_is_reported(client, digital_pdf):
    client.app.state.services.jobs.extraction.gemini = StubGemini(
        error=ExtractionError("upstream is down", code="GEMINI_ALL_MODELS_FAILED")
    )

    job_id = upload_files(client, [digital_pdf]).json()["jobs"][0]["job_id"]
    job = wait_for_job(client, job_id)

    assert job["status"] == "FAILED"
    assert job["progress"] == 100
    assert job["error"]["code"] == "GEMINI_ALL_MODELS_FAILED"
    assert "upstream is down" in job["error"]["message"]
    assert client.get(f"/api/v1/invoices/{job_id}/json").status_code == 409
