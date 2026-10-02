# Purpose: API route endpoints for creating, checking status, retrieving outputs, and reprocessing jobs.

import json
import logging
import uuid
from io import BytesIO
from pathlib import PurePosixPath
from typing import Literal, Optional, Tuple

import pymupdf
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.models import Block, Document, Job, Page
from app.schemas.schemas import JobCreateResponse, JobStatusResponse
from app.services.queue import queue_service
from app.services.storage import storage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["Jobs"])

# Soft cap to protect MinIO/API memory during MVP demos (~50 MB).
MAX_PDF_BYTES = 50 * 1024 * 1024


def _validate_pdf(upload: UploadFile) -> Tuple[bytes, str, int]:
    """
    Read and validate an uploaded PDF.
    Returns (pdf_bytes, safe_filename, page_count).
    """
    filename = upload.filename or "document.pdf"
    safe_filename = PurePosixPath(filename.replace("\\", "/")).name
    if not safe_filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    content_type = (upload.content_type or "").lower()
    if content_type and content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content type '{upload.content_type}'. Expected application/pdf.",
        )

    pdf_bytes = upload.file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds maximum allowed size of {MAX_PDF_BYTES // (1024 * 1024)} MB.",
        )
    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Invalid PDF: missing %PDF header.")

    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
            page_count = pdf.page_count
    except Exception as exc:
        logger.warning("PyMuPDF failed to open upload %s: %s", safe_filename, exc)
        raise HTTPException(status_code=400, detail="Unable to parse PDF contents.") from exc

    if page_count < 1:
        raise HTTPException(status_code=400, detail="PDF contains no pages.")

    return pdf_bytes, safe_filename, page_count


@router.post("", response_model=JobCreateResponse, status_code=202)
def upload_document(
    file: UploadFile = File(..., description="The multi-page PDF document to run OCR on"),
    db: Session = Depends(get_db),
):
    """
    Submits a PDF document to store in MinIO and schedules it for OCR extraction.
    """
    pdf_bytes, safe_filename, page_count = _validate_pdf(file)

    doc_id = uuid.uuid4()
    job_id = uuid.uuid4()

    try:
        storage_path = storage_service.upload_document(
            doc_id,
            BytesIO(pdf_bytes),
            safe_filename,
            content_type="application/pdf",
        )
    except Exception as exc:
        logger.exception("Failed to upload PDF to object storage")
        raise HTTPException(status_code=502, detail="Failed to store document in object storage.") from exc

    db_doc = Document(
        id=doc_id,
        filename=safe_filename,
        storage_path=storage_path,
        page_count=page_count,
    )
    db_job = Job(
        id=job_id,
        document_id=doc_id,
        status="queued",
    )
    db.add(db_doc)
    db.add(db_job)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        # Best-effort cleanup of orphaned object
        storage_service.delete_document(storage_path)
        logger.exception("Failed to persist document/job rows")
        raise HTTPException(status_code=500, detail="Failed to create job records.") from exc

    try:
        queue_service.enqueue_ocr_job(job_id, doc_id)
    except Exception as exc:
        logger.exception("Failed to enqueue OCR task for job_id=%s", job_id)
        db_job.status = "failed"
        db.commit()
        raise HTTPException(status_code=502, detail="Document stored but failed to enqueue OCR job.") from exc

    return JobCreateResponse(
        job_id=job_id,
        document_id=doc_id,
        status="queued",
        message="Document uploaded and OCR job enqueued successfully.",
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Retrieves current processing state, started/completed timestamps, and cumulative confidence.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")

    return JobStatusResponse(
        job_id=job.id,
        document_id=job.document_id,
        status=job.status,
        avg_confidence=job.avg_confidence,
        avg_quality_score=job.avg_quality_score,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/{job_id}/result")
def get_job_result(
    job_id: uuid.UUID,
    format: Literal[
        "json", "markdown", "txt", "searchable_pdf", "highlighted_pdf", "structured_pdf"
    ] = Query("json", description="Desired response payload format"),
    db: Session = Depends(get_db),
):
    """
    Retrieves finalized multilingual OCR output in either parsed Common JSON schema or Markdown layout.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    if job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is '{job.status}', not finished yet.",
        )

    _PDF_FILENAMES = {
        "searchable_pdf": "result_searchable.pdf",
        "highlighted_pdf": "result_highlighted.pdf",
        "structured_pdf": "result_structured.pdf",
    }
    if format in _PDF_FILENAMES:
        storage_path = f"results/{job_id}/{_PDF_FILENAMES[format]}"
    else:
        ext = {"markdown": "md", "txt": "txt"}.get(format, "json")
        storage_path = f"results/{job_id}/result.{ext}"

    try:
        raw = storage_service.download_document(storage_path)
    except ClientError as exc:
        logger.error("Result artifact missing for job_id=%s at %s", job_id, storage_path)
        raise HTTPException(
            status_code=404, detail="Result artifact not found in object storage."
        ) from exc

    if format == "markdown":
        return JSONResponse(content={"markdown": raw.decode("utf-8")})
    if format == "txt":
        return JSONResponse(content={"txt": raw.decode("utf-8")})
    if format in _PDF_FILENAMES:
        return Response(
            content=raw,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{_PDF_FILENAMES[format]}"'},
        )
    return JSONResponse(content=json.loads(raw))


@router.get("/{job_id}/pages/{page_number}")
def get_page_result(
    job_id: uuid.UUID,
    page_number: int,
    db: Session = Depends(get_db),
):
    """
    Retrieves layout details and preview image representing OCR bounding boxes for a single page.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    if job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is '{job.status}', not finished yet.",
        )

    page = (
        db.query(Page)
        .filter(Page.job_id == job_id, Page.page_number == page_number)
        .first()
    )
    if page is None:
        raise HTTPException(
            status_code=404, detail=f"Page {page_number} not found for job {job_id}."
        )

    block_rows = db.query(Block).filter(Block.page_id == page.id).all()
    # DB round-trip doesn't guarantee row order matches the original reading
    # order (no explicit sequence column on Block) — re-sort top-to-bottom,
    # left-to-right the same way normalize.sort_reading_order() does.
    block_rows.sort(key=lambda b: (b.bbox[1], b.bbox[0]))

    blocks = [
        {
            "block_id": f"p{page_number}_b{idx + 1}",
            "type": b.type,
            "text": b.content or "",
            "bbox": b.bbox,
            "confidence": b.confidence,
            "language": b.language,
            "engine_used": b.engine_used,
            "subtype": b.subtype,
            "table": b.table_data,
            "image_url": b.image_url,
            "caption": b.caption,
            "needs_review": b.needs_review,
            "review_status": b.review_status,
            "original_text": b.original_text,
            "corrected_text": b.content or "",
            "correction_applied": b.correction_applied,
            "correction_reason": b.correction_reason,
        }
        for idx, b in enumerate(block_rows)
    ]

    storage_path = f"results/{job_id}/page_{page_number}.png"
    try:
        image_preview_url = storage_service.generate_presigned_url(storage_path)
    except ClientError:
        image_preview_url = None

    return {
        "job_id": job_id,
        "page_number": page_number,
        "image_preview_url": image_preview_url,
        "ocr_attempt": page.ocr_attempt,
        "quality_score": page.quality_score,
        "blocks": blocks,
    }


@router.post("/{job_id}/reprocess", response_model=JobCreateResponse)
def reprocess_job(
    job_id: uuid.UUID,
    force_engine: Optional[Literal["paddleocr", "surya"]] = Query(
        None, description="Force a specific engine over router logic"
    ),
    db: Session = Depends(get_db),
):
    """
    Re-runs OCR extraction for the same document under a new job.

    force_engine is accepted but not yet honored — EngineRouter always runs
    its own confidence/table/mixed-script logic (ARCHITECTURE.md §9). Wiring
    a forced-engine override through the router and Celery task is a bigger
    change than this stub fix; log it so callers relying on it aren't
    silently misled.
    """
    old_job = db.query(Job).filter(Job.id == job_id).first()
    if old_job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")

    if force_engine is not None:
        logger.warning(
            "reprocess_job: force_engine=%s requested but not yet honored by "
            "EngineRouter/Celery task — running normal routing logic.",
            force_engine,
        )

    new_job_id = uuid.uuid4()
    db_job = Job(id=new_job_id, document_id=old_job.document_id, status="queued")
    db.add(db_job)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to create reprocess job row for document_id=%s", old_job.document_id)
        raise HTTPException(status_code=500, detail="Failed to create reprocess job.") from exc

    try:
        queue_service.enqueue_ocr_job(new_job_id, old_job.document_id)
    except Exception as exc:
        logger.exception("Failed to enqueue reprocess task for job_id=%s", new_job_id)
        db_job.status = "failed"
        db.commit()
        raise HTTPException(status_code=502, detail="Job created but failed to enqueue.") from exc

    return JobCreateResponse(
        job_id=new_job_id,
        document_id=old_job.document_id,
        status="queued",
        message="Reprocessing task enqueued successfully under new job.",
    )
