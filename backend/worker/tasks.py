# Purpose: Celery task that orchestrates the full OCR pipeline:
#   download PDF → rasterize pages → preprocess → route engines → normalise →
#   persist Page+Block rows to Postgres → upload JSON/Markdown artifacts to MinIO.
# Phase 0 item 4: stubs replaced with real preprocess + PaddleOCR + routing.

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF — used only to count pages here; rasterization is in preprocess

from app.core.db import SessionLocal
from app.models.models import Block, Document, Job, Page
from app.services.storage import storage_service
from worker.celery_app import celery_app
from worker.pipeline.normalize import convert_to_markdown, normalize_to_common_schema
from worker.pipeline.preprocess import preprocess_page, rasterize_pdf_page
from worker.pipeline.router import EngineRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level router — created once per Celery worker process so PaddleOCR
# model weights are loaded only on worker startup, not per task invocation.
# ---------------------------------------------------------------------------
_router: Optional[EngineRouter] = None


def _get_router() -> EngineRouter:
    global _router
    if _router is None:
        logger.info("Initialising EngineRouter for this worker process")
        _router = EngineRouter()
    return _router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _count_pdf_pages(pdf_bytes: bytes) -> int:
    """Returns the real page count from the PDF stream via PyMuPDF."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception as exc:
        logger.warning("Could not count PDF pages: %s — defaulting to 1", exc)
        return 1


def _process_single_page(
    router: EngineRouter,
    pdf_bytes: bytes,
    page_index: int,
    page_number: int,
    job_id: uuid.UUID,
) -> List[Dict[str, Any]]:
    """
    Rasterises, preprocesses, and runs OCR routing for one PDF page.

    Also uploads the raw rasterized page image to
    results/{job_id}/page_{page_number}.png so GET /jobs/{id}/pages/{n} has a
    real preview to point at instead of a URL for an object that never
    existed. Upload failure is logged but never fails the page — the OCR
    result matters more than the preview image.

    Returns a list of raw block dicts (no engine_used / block_id yet — those
    are added by the router and normaliser respectively).

    Any exception is caught, logged, and an error sentinel block is returned
    so the failure is visible in the output without aborting the whole job
    (ARCHITECTURE.md §15 reliability requirement).
    """
    try:
        image_bytes = rasterize_pdf_page(pdf_bytes, page_index)

        try:
            storage_service.client.put_object(
                Bucket=storage_service.bucket,
                Key=f"results/{job_id}/page_{page_number}.png",
                Body=image_bytes,
                ContentType="image/png",
            )
        except Exception:
            logger.warning(
                "Failed to upload page preview image for page %d", page_number, exc_info=True
            )

        image = preprocess_page(image_bytes)
        blocks = router.process_page(image)
        logger.info("Page %d: %d blocks extracted", page_number, len(blocks))
        return blocks
    except Exception as exc:
        logger.error(
            "Page %d processing failed: %s",
            page_number,
            exc,
            exc_info=True,
        )
        # Return a sentinel error block so downstream consumers know this page
        # had a problem but the job itself is not failed.
        return [
            {
                "text": f"[OCR ERROR on page {page_number}: {exc}]",
                "bbox": [0.0, 0.0, 0.0, 0.0],
                "confidence": 0.0,
                "language": "und",
                "type": "paragraph",
                "engine_used": "error",
            }
        ]


def _persist_pages_and_blocks(
    db,
    job: Job,
    normalized_output: Dict[str, Any],
) -> None:
    """
    Writes Page and Block ORM rows to Postgres for the completed job.

    Matches the DDL in ARCHITECTURE.md §11:
        JOBS → PAGES → BLOCKS
    """
    for page_data in normalized_output.get("pages", []):
        page_row = Page(
            id=uuid.uuid4(),
            job_id=job.id,
            page_number=page_data["page_number"],
            languages_detected=",".join(page_data.get("language_detected", [])),
        )
        db.add(page_row)
        db.flush()  # populate page_row.id before referencing it in Block rows

        for block_data in page_data.get("blocks", []):
            block_row = Block(
                id=uuid.uuid4(),
                page_id=page_row.id,
                type=block_data.get("type", "paragraph"),
                content=block_data.get("text", ""),
                bbox=block_data.get("bbox", [0.0, 0.0, 0.0, 0.0]),
                confidence=block_data.get("confidence", 0.0),
                language=block_data.get("language", "und"),
                engine_used=block_data.get("engine_used", "paddleocr"),
            )
            db.add(block_row)

    db.commit()
    logger.info("Persisted pages + blocks to DB for job_id=%s", job.id)


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(name="worker.tasks.process_ocr", bind=True)
def process_ocr(self, job_id_str: str, document_id_str: str) -> Dict[str, Any]:
    """
    Background Celery task that runs the full OCR pipeline for a document:

    1. Downloads raw PDF from MinIO.
    2. Counts real page count and updates the Document record if needed.
    3. For each page:
       a. Rasterizes at 200 DPI via PyMuPDF.
       b. Deskews + denoises via OpenCV.
       c. Routes through PaddleOCR → confidence check → optional Surya fallback.
    4. Normalizes all page outputs to the Common Output Schema (ARCHITECTURE.md §10).
    5. Persists Page + Block rows to Postgres.
    6. Uploads structured JSON and Markdown to MinIO.
    7. Marks job as done (or failed on unrecoverable error).
    """
    logger.info(
        "Starting OCR task | job_id=%s document_id=%s",
        job_id_str,
        document_id_str,
    )
    job_id = uuid.UUID(job_id_str)
    doc_id = uuid.UUID(document_id_str)

    self.update_state(state="PROGRESS", meta={"percent": 5, "status": "Initialising"})

    db = SessionLocal()
    try:
        # ---- Load DB records --------------------------------------------
        job = db.query(Job).filter(Job.id == job_id).first()
        document = db.query(Document).filter(Document.id == doc_id).first()
        if job is None or document is None:
            raise ValueError(
                f"Job or Document not found (job_id={job_id}, document_id={doc_id})"
            )

        _set_job_status(db, job, "processing")
        self.update_state(
            state="PROGRESS", meta={"percent": 10, "status": "Downloading PDF"}
        )

        # ---- Download PDF from MinIO ------------------------------------
        pdf_bytes = storage_service.download_document(document.storage_path)
        logger.info(
            "Downloaded %d bytes from %s", len(pdf_bytes), document.storage_path
        )

        # ---- Resolve real page count ------------------------------------
        real_page_count = _count_pdf_pages(pdf_bytes)
        if document.page_count != real_page_count:
            logger.info(
                "Correcting page_count %d → %d for document_id=%s",
                document.page_count,
                real_page_count,
                doc_id,
            )
            document.page_count = real_page_count
            db.commit()

        # ---- Process each page ------------------------------------------
        router = _get_router()
        raw_pages: Dict[int, List[Dict[str, Any]]] = {}

        self.update_state(
            state="PROGRESS",
            meta={"percent": 20, "status": "Starting page processing"},
        )

        for page_index in range(real_page_count):
            page_number = page_index + 1  # 1-based for the output schema
            progress_pct = 20 + int(60 * page_index / real_page_count)
            self.update_state(
                state="PROGRESS",
                meta={
                    "percent": progress_pct,
                    "status": f"Processing page {page_number}/{real_page_count}",
                },
            )

            blocks = _process_single_page(router, pdf_bytes, page_index, page_number, job_id)
            raw_pages[page_number] = blocks

        # ---- Normalise --------------------------------------------------
        self.update_state(
            state="PROGRESS", meta={"percent": 82, "status": "Normalising output schema"}
        )
        normalized_output = normalize_to_common_schema(doc_id, document.filename, raw_pages)
        markdown_output = convert_to_markdown(normalized_output)

        # ---- Persist Page + Block rows to Postgres ----------------------
        self.update_state(
            state="PROGRESS", meta={"percent": 88, "status": "Persisting results to DB"}
        )
        _persist_pages_and_blocks(db, job, normalized_output)

        # ---- Upload artifacts to MinIO ----------------------------------
        self.update_state(
            state="PROGRESS", meta={"percent": 93, "status": "Uploading artifacts"}
        )
        storage_service.upload_result(
            job_id, json.dumps(normalized_output, ensure_ascii=False, indent=2), "json"
        )
        storage_service.upload_result(job_id, markdown_output, "markdown")

        # ---- Mark job done ----------------------------------------------
        avg_conf = float(normalized_output["metadata"]["avg_confidence"])
        _set_job_status(db, job, "done", avg_confidence=avg_conf, completed=True)

        self.update_state(
            state="SUCCESS", meta={"percent": 100, "status": "Completed"}
        )
        logger.info(
            "OCR task complete | job_id=%s avg_confidence=%.4f pages=%d",
            job_id_str,
            avg_conf,
            real_page_count,
        )
        return {
            "status": "success",
            "job_id": job_id_str,
            "avg_confidence": avg_conf,
            "page_count": real_page_count,
        }

    except Exception as exc:
        logger.exception("OCR task failed | job_id=%s error=%s", job_id_str, exc)
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job is not None:
                _set_job_status(db, job, "failed", completed=True)
        except Exception:
            logger.exception(
                "Failed to mark job %s as failed during exception handler", job_id_str
            )
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise

    finally:
        db.close()
