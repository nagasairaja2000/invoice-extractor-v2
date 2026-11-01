"""Tests for job lifecycle, pub/sub, history and cleanup."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import ExtractionError, JobNotFoundError
from app.core.security import SavedUpload
from app.schemas.job import Job, JobEventType, JobStatus
from app.services.job_manager import JobManager
from app.services.storage import Storage
from conftest import build_result


class FakeExtraction:
    def __init__(self, result=None, error=None, gate: asyncio.Event | None = None):
        self.result = result if result is not None else build_result()
        self.error = error
        self.gate = gate

    async def process(self, *, job_id, file_path, filename, emit):
        await emit("VALIDATING", 5, "Validating PDF structure")
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        await emit("CALLING_LLM", 65, "Calling Gemini")
        return self.result


def make_upload(tmp_path, name: str = "invoice.pdf") -> SavedUpload:
    path = tmp_path / f"{uuid.uuid4().hex}.pdf"
    path.write_bytes(b"%PDF-1.4\nsynthetic\n")
    return SavedUpload(
        path=path,
        original_name=name,
        display_name=name,
        size=path.stat().st_size,
        sha256="0" * 64,
    )


async def wait_terminal(manager: JobManager, job_id: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job.status.is_terminal:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish in {timeout}s")


async def wait_for_status(manager: JobManager, job_id: str, status: JobStatus, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.get_job(job_id).status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job {job_id} never reached {status}")


@pytest.fixture
def storage(settings) -> Storage:
    return Storage(settings)


@pytest.fixture
async def manager(settings, storage):
    instance = JobManager(settings, FakeExtraction(), storage)
    await instance.start()
    yield instance
    await instance.shutdown()


@pytest.mark.asyncio
async def test_successful_job_is_persisted(manager, storage, tmp_path):
    upload = make_upload(tmp_path)

    job = manager.create_and_start(upload)
    completed = await wait_terminal(manager, job.id)

    assert completed.status == JobStatus.COMPLETED
    assert completed.progress == 100
    assert completed.stage == "DONE"
    assert completed.result is not None
    assert completed.result.invoice.invoice_number == "TEST-2026-001"
    assert completed.pdf_type == "digital"
    assert upload.path.exists()

    artifact = storage.load_result(job.id)
    assert artifact is not None
    assert artifact["job_id"] == job.id
    assert artifact["filename"] == "invoice.pdf"
    assert artifact["result"]["invoice"]["invoice_number"] == "TEST-2026-001"


@pytest.mark.asyncio
async def test_app_error_marks_job_failed(manager, tmp_path):
    manager.extraction = FakeExtraction(error=ExtractionError("upstream down"))

    job = manager.create_and_start(make_upload(tmp_path))
    failed = await wait_terminal(manager, job.id)

    assert failed.status == JobStatus.FAILED
    assert failed.stage == "ERROR"
    assert failed.progress == 100
    assert failed.error is not None
    assert failed.error.code == "EXTRACTION_FAILED"
    assert failed.error.message == "upstream down"


@pytest.mark.asyncio
async def test_unexpected_error_is_wrapped(manager, tmp_path):
    manager.extraction = FakeExtraction(error=RuntimeError("kaboom"))

    job = manager.create_and_start(make_upload(tmp_path))
    failed = await wait_terminal(manager, job.id)

    assert failed.status == JobStatus.FAILED
    assert failed.error is not None
    assert failed.error.code == "INTERNAL_ERROR"
    assert "unexpectedly" in failed.error.message


@pytest.mark.asyncio
async def test_delete_removes_job_artifact_and_upload(manager, storage, tmp_path):
    upload = make_upload(tmp_path)
    job = manager.create_and_start(upload)
    await wait_terminal(manager, job.id)

    manager.delete_job(job.id)

    assert not upload.path.exists()
    assert storage.load_result(job.id) is None
    with pytest.raises(JobNotFoundError):
        manager.get_job(job.id)


@pytest.mark.asyncio
async def test_delete_unknown_job_raises(manager):
    with pytest.raises(JobNotFoundError):
        manager.delete_job("missing")


def test_list_jobs_sorts_filters_and_limits(manager):
    now = datetime.now(timezone.utc)
    manager._jobs.clear()
    manager._jobs["old"] = Job(
        id="old",
        filename="old.pdf",
        status=JobStatus.COMPLETED,
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )
    manager._jobs["new"] = Job(
        id="new",
        filename="new.pdf",
        status=JobStatus.FAILED,
        created_at=now,
        updated_at=now,
    )

    assert [job.id for job in manager.list_jobs()] == ["new", "old"]
    assert [job.id for job in manager.list_jobs(status=JobStatus.COMPLETED)] == ["old"]
    assert [job.id for job in manager.list_jobs(limit=1)] == ["new"]


@pytest.mark.asyncio
async def test_history_is_loaded_from_artifacts(manager, storage):
    now = datetime.now(timezone.utc)
    storage.save_result(
        job_id="history-1",
        filename="archived.pdf",
        created_at=now - timedelta(minutes=5),
        completed_at=now,
        result=build_result(),
    )

    manager._load_history()
    job = manager.get_job("history-1")

    assert job.status == JobStatus.COMPLETED
    assert job.filename == "archived.pdf"
    assert job.result is not None
    assert job.file_path is None


@pytest.mark.asyncio
async def test_subscribe_sends_snapshot(manager, tmp_path):
    job = manager.create_and_start(make_upload(tmp_path))
    await wait_terminal(manager, job.id)

    queue = manager.subscribe(job.id)
    snapshot = queue.get_nowait()

    assert snapshot.type == JobEventType.SNAPSHOT
    assert snapshot.job_id == job.id
    assert snapshot.status == JobStatus.COMPLETED
    assert snapshot.data["job"]["id"] == job.id

    manager.unsubscribe(job.id, queue)
    assert job.id not in manager._subscribers
    assert manager._subscribers == {}


@pytest.mark.asyncio
async def test_progress_events_are_published(manager, tmp_path):
    gate = asyncio.Event()
    manager.extraction = FakeExtraction(gate=gate)
    job = manager.create_and_start(make_upload(tmp_path))
    queue = manager.subscribe(job.id)
    snapshot = queue.get_nowait()
    assert snapshot.type == JobEventType.SNAPSHOT or snapshot.status == JobStatus.QUEUED

    gate.set()
    event_types = []
    terminal = None
    while terminal is None:
        event = await asyncio.wait_for(queue.get(), timeout=2)
        event_types.append(event.type)
        if event.status.is_terminal:
            terminal = event

    assert JobEventType.PROGRESS in event_types
    assert terminal.status == JobStatus.COMPLETED
    assert terminal.progress == 100


@pytest.mark.asyncio
async def test_cleanup_expired_jobs(manager, storage, tmp_path):
    upload = make_upload(tmp_path)
    job = manager.create_and_start(upload)
    await wait_terminal(manager, job.id)
    manager.get_job(job.id).updated_at = datetime.now(timezone.utc) - timedelta(hours=2)

    removed = manager.cleanup_expired()

    assert removed == 1
    assert manager.list_jobs() == []
    assert not upload.path.exists()
    assert storage.load_result(job.id) is None


@pytest.mark.asyncio
async def test_cleanup_keeps_fresh_jobs(manager, tmp_path):
    job = manager.create_and_start(make_upload(tmp_path))
    await wait_terminal(manager, job.id)

    assert manager.cleanup_expired() == 0
    assert manager.get_job(job.id).status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_concurrency_limit_queues_excess_jobs(settings, storage, tmp_path):
    gate = asyncio.Event()
    limited = JobManager(
        settings.model_copy(update={"max_concurrent_jobs": 1}),
        FakeExtraction(gate=gate),
        storage,
    )
    await limited.start()
    try:
        first = limited.create_and_start(make_upload(tmp_path, "first.pdf"))
        await wait_for_status(limited, first.id, JobStatus.PROCESSING)
        second = limited.create_and_start(make_upload(tmp_path, "second.pdf"))
        await asyncio.sleep(0.05)

        assert limited.get_job(second.id).status == JobStatus.QUEUED

        gate.set()
        await wait_terminal(limited, first.id)
        await wait_terminal(limited, second.id)
        assert limited.get_job(second.id).status == JobStatus.COMPLETED
    finally:
        gate.set()
        await limited.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_running_jobs(settings, storage, tmp_path):
    gate = asyncio.Event()
    paused = JobManager(settings, FakeExtraction(gate=gate), storage)
    await paused.start()
    job = paused.create_and_start(make_upload(tmp_path))
    await wait_for_status(paused, job.id, JobStatus.PROCESSING)

    await paused.shutdown()

    assert paused.get_job(job.id).status == JobStatus.FAILED
    assert paused.get_job(job.id).message == "Job was cancelled."
