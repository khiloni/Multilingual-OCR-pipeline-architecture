# Purpose: Enqueuing OCR processing tasks to Redis/Celery queue broker without blocking API threads.

import logging
from uuid import UUID

logger = logging.getLogger(__name__)


class QueueService:
    """
    Handles scheduling, triggering, and inspecting asynchronous Celery jobs.
    """

    def __init__(self) -> None:
        logger.info("Initializing Celery Queue service connection (lazy setup)")

    def enqueue_ocr_job(self, job_id: UUID, document_id: UUID) -> str:
        """
        Pushes an OCR extraction request to the Redis/Celery queue.
        Returns the Celery task ID.
        """
        # Import by app name to avoid circular imports with worker.tasks → storage.
        from worker.celery_app import celery_app

        async_result = celery_app.send_task(
            "worker.tasks.process_ocr",
            args=[str(job_id), str(document_id)],
        )
        logger.info(
            "Enqueued Celery OCR task task_id=%s job_id=%s document_id=%s",
            async_result.id,
            job_id,
            document_id,
        )
        return async_result.id

    def get_task_status(self, task_id: str) -> str:
        """
        Retrieves task execution state from Redis broker backend (e.g. PENDING, STARTED, SUCCESS).
        """
        from worker.celery_app import celery_app

        result = celery_app.AsyncResult(task_id)
        logger.info("Checking task status for task_id=%s state=%s", task_id, result.state)
        return result.state


queue_service = QueueService()
