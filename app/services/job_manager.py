"""In-process job manager with pub/sub progress events and TTL cleanup.

A single-process design keeps the project dependency-free (no Redis/Celery)
while still giving real background processing, bounded concurrency and live
progress over WebSockets.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.core.exceptions import AppError, JobNotFoundError
from app.core.security import SavedUpload
from app.schemas.common import ErrorInfo
from app.schemas.invoice import ExtractionResult
from app.schemas.job import Job, JobEvent, JobEventType, JobStatus
from app.services.extraction_service import ExtractionService
from app.services.storage import Storage
from app.utils.files import remove_file

logger = logging.getLogger(__name__)

_SUBSCRIBER_QUEUE_SIZE = 64
_CLEANUP_INTERVAL_SECONDS = 300


class JobManager:
    def __init__(
        self,
        settings: Settings,
        extraction_service: ExtractionService,
        storage: Storage,
    ) -> None:
        self.settings = settings
        self.extraction = extraction_service
        self.storage = storage

        self._jobs: dict[str, Job] = {}
        self._subscribers: dict[str, set[asyncio.Queue[JobEvent]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(max(settings.max_concurrent_jobs, 1))
        self._cleanup_task: asyncio.Task[None] | None = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._load_history()
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="job-cleanup")
        logger.info(
            "JobManager started (max_concurrent_jobs=%s, known_jobs=%s)",
            self.settings.max_concurrent_jobs,
            len(self._jobs),
        )

    async def shutdown(self) -> None:
        self._started = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

        for queues in self._subscribers.values():
            for queue in queues:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(
                        JobEvent(
                            type=JobEventType.FAILED,
                            job_id="",
                            status=JobStatus.FAILED,
                            message="Server is shutting down.",
                        )
                    )
        self._subscribers.clear()

    # ------------------------------------------------------------------
    # Creation / lookup
    # ------------------------------------------------------------------
    def create_and_start(self, upload: SavedUpload) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            filename=upload.display_name,
            file_path=str(upload.path),
            status=JobStatus.QUEUED,
            message="Queued for processing",
        )
        self._jobs[job.id] = job

        task = asyncio.create_task(self._run(job), name=f"job-{job.id}")
        self._tasks[job.id] = task
        task.add_done_callback(lambda _task, job_id=job.id: self._tasks.pop(job_id, None))

        logger.info("Job %s queued for %s", job.id, job.filename)
        return job

    def get_job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(f"No job found with id {job_id}.")
        return job

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        limit: int | None = None,
    ) -> list[Job]:
        jobs = list(self._jobs.values())
        if status is not None:
            jobs = [job for job in jobs if job.status == status]
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        if limit is not None:
            jobs = jobs[:limit]
        return jobs

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------
    def delete_job(self, job_id: str) -> None:
        job = self.get_job(job_id)

        task = self._tasks.pop(job_id, None)
        if task and not task.done():
            task.cancel()

        self._jobs.pop(job_id, None)
        self._subscribers.pop(job_id, None)
        self.storage.delete_result(job_id)

        if job.file_path:
            remove_file(job.file_path)

        logger.info("Job %s deleted", job_id)

    # ------------------------------------------------------------------
    # Pub/sub
    # ------------------------------------------------------------------
    def subscribe(self, job_id: str) -> asyncio.Queue[JobEvent]:
        job = self.get_job(job_id)
        queue: asyncio.Queue[JobEvent] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.setdefault(job_id, set()).add(queue)

        queue.put_nowait(self._snapshot(job))
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[JobEvent]) -> None:
        subscribers = self._subscribers.get(job_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(job_id, None)

    def _snapshot(self, job: Job) -> JobEvent:
        return JobEvent(
            type=JobEventType.SNAPSHOT,
            job_id=job.id,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            message=job.message,
            data={"job": job.to_public().model_dump(mode="json")},
        )

    def _publish(self, event: JobEvent) -> None:
        for queue in self._subscribers.get(event.job_id, set()):
            if queue.full():
                # Slow consumer: drop the oldest event so the newest state wins.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def _emit_progress(self, job: Job):
        async def emit(stage: str, progress: int, message: str) -> None:
            job.stage = stage
            job.progress = max(1, min(int(progress), 99))
            job.message = message
            job.updated_at = datetime.now(timezone.utc)
            self._publish(
                JobEvent(
                    type=JobEventType.PROGRESS,
                    job_id=job.id,
                    status=job.status,
                    stage=stage,
                    progress=job.progress,
                    message=message,
                )
            )

        return emit

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    async def _run(self, job: Job) -> None:
        try:
            async with self._semaphore:
                job.status = JobStatus.PROCESSING
                job.stage = "QUEUED"
                job.progress = 1
                job.message = "Starting extraction"
                job.updated_at = datetime.now(timezone.utc)

                logger.info("Job %s processing started", job.id)

                result = await self.extraction.process(
                    job_id=job.id,
                    file_path=job.file_path or "",
                    filename=job.filename,
                    emit=self._emit_progress(job),
                )

                completed_at = datetime.now(timezone.utc)
                job.result = result
                job.pdf_type = result.pdf_type
                job.approach = result.approach
                job.status = JobStatus.COMPLETED
                job.stage = "DONE"
                job.progress = 100
                job.message = "Extraction complete"
                job.updated_at = completed_at

                self._persist(job, result, completed_at)
                self._publish(
                    JobEvent(
                        type=JobEventType.COMPLETED,
                        job_id=job.id,
                        status=job.status,
                        stage=job.stage,
                        progress=100,
                        message=job.message,
                        data={"job": job.to_public().model_dump(mode="json")},
                    )
                )
                logger.info("Job %s completed", job.id)

        except asyncio.CancelledError:
            if self._jobs.get(job.id) is job:
                job.status = JobStatus.FAILED
                job.message = "Job was cancelled."
                job.updated_at = datetime.now(timezone.utc)
            logger.info("Job %s cancelled", job.id)
            raise

        except AppError as exc:
            self._fail(job, exc.code, exc.message, exc.details)
            logger.warning("Job %s failed: %s", job.id, exc.message)

        except Exception as exc:  # noqa: BLE001 - never let a job crash the process
            logger.exception("Unhandled failure in job %s", job.id)
            self._fail(job, "INTERNAL_ERROR", "Extraction failed unexpectedly.", str(exc)[:500])

    def _persist(self, job: Job, result: ExtractionResult, completed_at: datetime) -> None:
        try:
            self.storage.save_result(
                job_id=job.id,
                filename=job.filename,
                created_at=job.created_at,
                completed_at=completed_at,
                result=result,
            )
        except OSError:
            logger.exception("Could not persist artifact for job %s", job.id)
            job.result = job.result or result
            # Persistence failure is not fatal for the in-memory result.

    def _fail(self, job: Job, code: str, message: str, details: object = None) -> None:
        job.status = JobStatus.FAILED
        job.stage = "ERROR"
        job.progress = 100
        job.message = message
        job.error = ErrorInfo(code=code, message=message, details=details)
        job.updated_at = datetime.now(timezone.utc)

        self._publish(
            JobEvent(
                type=JobEventType.FAILED,
                job_id=job.id,
                status=job.status,
                stage=job.stage,
                progress=100,
                message=message,
                data={
                    "job": job.to_public().model_dump(mode="json"),
                    "error": job.error.model_dump(),
                },
            )
        )

    # ------------------------------------------------------------------
    # History / cleanup
    # ------------------------------------------------------------------
    def _load_history(self) -> None:
        for artifact in self.storage.iter_artifacts():
            try:
                job_id = str(artifact["job_id"])
                if job_id in self._jobs:
                    continue

                result = ExtractionResult.model_validate(artifact["result"])
                created_at = datetime.fromisoformat(artifact["created_at"])
                completed_at = datetime.fromisoformat(
                    artifact.get("completed_at") or artifact["created_at"]
                )

                self._jobs[job_id] = Job(
                    id=job_id,
                    filename=str(artifact.get("filename") or "invoice.pdf"),
                    status=JobStatus.COMPLETED,
                    stage="DONE",
                    progress=100,
                    message="Extraction complete",
                    created_at=created_at,
                    updated_at=completed_at,
                    pdf_type=result.pdf_type,
                    approach=result.approach,
                    result=result,
                )
            except Exception:  # noqa: BLE001 - a bad artifact must not block startup
                logger.warning("Skipping invalid artifact: %s", artifact, exc_info=True)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
                self.cleanup_expired()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the loop alive
                logger.exception("Job cleanup iteration failed")

    def cleanup_expired(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.job_ttl_hours)
        expired = [
            job.id
            for job in self._jobs.values()
            if job.status.is_terminal and job.updated_at < cutoff
        ]
        for job_id in expired:
            try:
                self.delete_job(job_id)
            except Exception:  # noqa: BLE001
                logger.warning("Could not delete expired job %s", job_id, exc_info=True)
        if expired:
            logger.info("Cleaned up %s expired job(s)", len(expired))
        return len(expired)
