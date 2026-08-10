# Purpose: Define the Celery task runner executing PDF page split, preprocessing, routing, and normalization.
# Phase 0: wires real DB status + MinIO download; OCR engines remain stubbed until later phases.

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.db import SessionLocal
from app.models.models import Document, Job
from app.services.storage import storage_service
from worker.celery_app import celery_app
from worker.pipeline.normalize import convert_to_markdown, normalize_to_common_schema

logger = logging.getLogger(__name__)


def _set_job_status(
    db,
    job: Job,
    status: str,
    *,
    avg_confidence: Optional[float] = None,
    completed: bool = False,
) -> None:
    job.status = status
    if avg_confidence is not None:
        job.avg_confidence = avg_confidence
    if completed:
        job.completed_at = datetime.utcnow()
    db.commit()


@celery_app.task(name="worker.tasks.process_ocr", bind=True)
def process_ocr(self, job_id_str: str, document_id_str: str) -> Dict[str, Any]:
    """
    Background Celery task that processes a document:
      1. Downloads raw PDF from Object Storage.
      2. Rasterizes pages into images.
      3. For each page: preprocess + route engines (stubbed in Phase 0).
      4. Normalizes outputs to JSON Common Schema.
      5. Uploads final artifacts to MinIO.
      6. Updates database job status to done/failed.
    """
    logger.info("Starting Celery OCR task for job_id=%s, document_id=%s", job_id_str, document_id_str)
    job_id = uuid.UUID(job_id_str)
    doc_id = uuid.UUID(document_id_str)

    self.update_state(state="PROGRESS", meta={"percent": 10, "status": "Downloading document"})

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        document = db.query(Document).filter(Document.id == doc_id).first()
        if job is None or document is None:
            raise ValueError(f"Job or document not found (job_id={job_id}, document_id={doc_id})")

        _set_job_status(db, job, "processing")

        pdf_bytes = storage_service.download_document(document.storage_path)
        logger.info("Downloaded %s bytes from %s", len(pdf_bytes), document.storage_path)

        self.update_state(state="PROGRESS", meta={"percent": 30, "status": "Rasterizing PDF pages"})
        # Phase 0: OCR path still stubbed — use document.page_count for mock loop sizing.
        page_count = document.page_count or 1
        raw_pages: Dict[int, List[Dict[str, Any]]] = {}

        for p_idx in range(1, page_count + 1):
            self.update_state(state="PROGRESS", meta={"percent": 50, "status": f"Processing page {p_idx}"})
            # Stub blocks until PaddleOCR/Surya engines are wired.
            blocks = [
                {
                    "text": "This is a paragraph of extracted text.",
                    "bbox": [10.0, 15.0, 300.0, 45.0],
                    "confidence": 0.94,
                    "language": "en",
                    "type": "paragraph",
                }
            ]
            raw_pages[p_idx] = blocks

        self.update_state(state="PROGRESS", meta={"percent": 80, "status": "Normalizing schemas"})
        normalized_output = normalize_to_common_schema(doc_id, document.filename, raw_pages)
        markdown_output = convert_to_markdown(normalized_output)

        storage_service.upload_result(job_id, str(normalized_output), "json")
        storage_service.upload_result(job_id, markdown_output, "markdown")

        avg_conf = float(normalized_output["metadata"]["avg_confidence"])
        _set_job_status(db, job, "done", avg_confidence=avg_conf, completed=True)

        self.update_state(state="SUCCESS", meta={"status": "Completed OCR pipeline processing"})
        return {
            "status": "success",
            "job_id": job_id_str,
            "avg_confidence": avg_conf,
        }

    except Exception as e:
        logger.exception("OCR Task failed for job_id=%s", job_id_str)
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job is not None:
                _set_job_status(db, job, "failed", completed=True)
        except Exception:
            logger.exception("Failed to mark job %s as failed", job_id_str)
        self.update_state(state="FAILURE", meta={"error": str(e)})
        raise
    finally:
        db.close()
