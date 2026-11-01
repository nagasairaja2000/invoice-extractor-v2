"""WebSocket progress stream for a single extraction job."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import get_websocket_state
from app.core.exceptions import JobNotFoundError
from app.schemas.job import JobEventType, JobStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

HEARTBEAT_SECONDS = 20
CLOSE_NOT_FOUND = 4404


@router.websocket("/ws/jobs/{job_id}")
async def job_events(websocket: WebSocket, job_id: str) -> None:
    state = get_websocket_state(websocket)
    await websocket.accept()

    try:
        queue = state.jobs.subscribe(job_id)
    except JobNotFoundError:
        await websocket.send_json(
            {
                "type": "failed",
                "job_id": job_id,
                "status": "FAILED",
                "message": "Job not found.",
                "code": "JOB_NOT_FOUND",
            }
        )
        await websocket.close(code=CLOSE_NOT_FOUND)
        return

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # Keeps proxies from closing an idle connection.
                await websocket.send_json({"type": "ping", "job_id": job_id})
                continue

            await websocket.send_json(event.model_dump(mode="json"))

            if event.type in {JobEventType.COMPLETED, JobEventType.FAILED} or event.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
            }:
                await websocket.close(code=1000)
                return

    except (WebSocketDisconnect, RuntimeError):
        # Client navigated away or the connection dropped; nothing to clean up
        # beyond the subscriber set.
        pass
    except Exception:  # noqa: BLE001 - a socket error must not log a stack trace storm
        logger.debug("WebSocket error for job %s", job_id, exc_info=True)
    finally:
        state.jobs.unsubscribe(job_id, queue)
