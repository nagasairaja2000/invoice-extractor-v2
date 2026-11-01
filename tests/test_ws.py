"""WebSocket progress stream tests."""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import StubGemini, upload_files, wait_for_job


def test_unknown_job_gets_error_and_close(client):
    with client.websocket_connect("/ws/jobs/unknown") as websocket:
        message = websocket.receive_json()

        assert message["type"] == "failed"
        assert message["code"] == "JOB_NOT_FOUND"
        assert message["status"] == "FAILED"

        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_json()

    assert excinfo.value.code == 4404


def test_late_subscriber_receives_snapshot_then_close(client, digital_pdf):
    client.app.state.services.jobs.extraction.gemini = StubGemini()
    job_id = upload_files(client, [digital_pdf]).json()["jobs"][0]["job_id"]
    wait_for_job(client, job_id)

    with client.websocket_connect(f"/ws/jobs/{job_id}") as websocket:
        message = websocket.receive_json()

        assert message["type"] == "snapshot"
        assert message["status"] == "COMPLETED"
        assert message["data"]["job"]["result"]["invoice"]["invoice_number"] == "TEST-2026-001"

        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_json()

    assert excinfo.value.code == 1000


def test_live_progress_reaches_terminal_event(client, digital_pdf):
    client.app.state.services.jobs.extraction.gemini = StubGemini()
    job_id = upload_files(client, [digital_pdf]).json()["jobs"][0]["job_id"]

    seen: list[str] = []
    with client.websocket_connect(f"/ws/jobs/{job_id}") as websocket:
        while True:
            message = websocket.receive_json()
            seen.append(message["type"])
            if message.get("status") in {"COMPLETED", "FAILED"}:
                terminal = message
                break

    assert terminal["status"] == "COMPLETED"
    assert terminal["progress"] == 100
    assert seen[0] == "snapshot"
    assert "completed" in {message_type for message_type in seen} or "snapshot" in seen


def test_failed_job_emits_failed_event(client, digital_pdf):
    from app.core.exceptions import ExtractionError

    client.app.state.services.jobs.extraction.gemini = StubGemini(
        error=ExtractionError("boom", code="EXTRACTION_FAILED")
    )
    job_id = upload_files(client, [digital_pdf]).json()["jobs"][0]["job_id"]

    with client.websocket_connect(f"/ws/jobs/{job_id}") as websocket:
        while True:
            message = websocket.receive_json()
            if message.get("status") in {"COMPLETED", "FAILED"}:
                terminal = message
                break

    assert terminal["type"] in {"failed", "snapshot"}
    assert terminal["status"] == "FAILED"
    error = terminal["data"].get("error") or terminal["data"]["job"]["error"]
    assert error["code"] == "EXTRACTION_FAILED"
