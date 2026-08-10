# Purpose: API route endpoints for creating, checking status, retrieving outputs, and reprocessing jobs.

import logging
import uuid
from io import BytesIO
from pathlib import PurePosixPath
from typing import Literal, Optional, Tuple

import pymupdf
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.models import Document, Job
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
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/{job_id}/result")
def get_job_result(
    job_id: uuid.UUID,
    format: Literal["json", "markdown"] = Query("json", description="Desired response payload format"),
    db: Session = Depends(get_db),
):
    """
    Retrieves finalized multilingual OCR output in either parsed Common JSON schema or Markdown layout.
    """
    # TODO: Fetch job from DB and verify status == "done"
    # If done, load extraction results from storage_service or db blocks and format.
    if format == "markdown":
        return JSONResponse(content={"markdown": "# Decoded PDF Heading\n\nThis is placeholder text."})

    import datetime

    return {
        "document_id": uuid.uuid4(),
        "filename": "placeholder.pdf",
        "page_count": 1,
        "pages": [
            {
                "page_number": 1,
                "language_detected": ["en"],
                "blocks": [
                    {
                        "block_id": "p1_b1",
                        "type": "paragraph",
                        "text": "This is a placeholder of extracted OCR text content.",
                        "bbox": [10.0, 20.0, 100.0, 50.0],
                        "confidence": 0.95,
                        "language": "en",
                        "engine_used": "paddleocr",
                    }
                ],
            }
        ],
        "metadata": {
            "processed_at": datetime.datetime.utcnow().isoformat(),
            "avg_confidence": 0.95,
            "low_confidence_pages": [],
        },
    }


@router.get("/{job_id}/pages/{page_number}")
def get_page_result(
    job_id: uuid.UUID,
    page_number: int,
    db: Session = Depends(get_db),
):
    """
    Retrieves layout details and preview image representing OCR bounding boxes for a single page.
    """
    return {
        "job_id": job_id,
        "page_number": page_number,
        "image_preview_url": f"http://localhost:9000/docscribe-storage/results/{job_id}/page_{page_number}.png",
        "blocks": [],
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
    Re-runs OCR extraction using alternate settings or forcing a specific engine path.
    """
    new_job_id = uuid.uuid4()
    return JobCreateResponse(
        job_id=new_job_id,
        document_id=uuid.uuid4(),
        status="queued",
        message="Reprocessing task enqueued successfully under new job.",
    )
