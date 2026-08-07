# Purpose: API route endpoints for creating, checking status, retrieving outputs, and reprocessing jobs.
# Future TODOs: Add rate limits, file validation (mime-type and size checks), and webhook trigger dispatches.

import uuid
from typing import Literal
from fastapi import APIRouter, UploadFile, File, Depends, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.schemas.schemas import JobCreateResponse, JobStatusResponse, CommonOutputSchema
from app.services.storage import storage_service
from app.services.queue import queue_service

router = APIRouter(prefix="/jobs", tags=["Jobs"])

@router.post("", response_model=JobCreateResponse, status_code=202)
def upload_document(
    file: UploadFile = File(..., description="The multi-page PDF document to run OCR on"),
    db: Session = Depends(get_db)
):
    """
    Submits a PDF document to store in MinIO and schedules it for OCR extraction.
    """
    # 1. Generate UUIDs for document and job
    doc_id = uuid.uuid4()
    job_id = uuid.uuid4()
    
    # 2. Lazy storage upload check
    storage_path = storage_service.upload_document(doc_id, file.file, file.filename)
    
    # 3. Save placeholder db entries
    # TODO: db_doc = Document(id=doc_id, filename=file.filename, storage_path=storage_path, page_count=1)
    # TODO: db_job = Job(id=job_id, document_id=doc_id, status="queued")
    # db.add(db_doc)
    # db.add(db_job)
    # db.commit()

    # 4. Enqueue processing task
    queue_service.enqueue_ocr_job(job_id, doc_id)

    return JobCreateResponse(
        job_id=job_id,
        document_id=doc_id,
        status="queued",
        message="Document uploaded and OCR job enqueued successfully."
    )

@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Retrieves current processing state, started/completed timestamps, and cumulative confidence.
    """
    # TODO: Query database Job record.
    # If not found, return 404.
    import datetime
    return JobStatusResponse(
        job_id=job_id,
        document_id=uuid.uuid4(),
        status="processing",
        avg_confidence=None,
        started_at=datetime.datetime.utcnow(),
        completed_at=None
    )

@router.get("/{job_id}/result")
def get_job_result(
    job_id: uuid.UUID,
    format: Literal["json", "markdown"] = Query("json", description="Desired response payload format"),
    db: Session = Depends(get_db)
):
    """
    Retrieves finalized multilingual OCR output in either parsed Common JSON schema or Markdown layout.
    """
    # TODO: Fetch job from DB and verify status == "done"
    # If done, load extraction results from storage_service or db blocks and format.
    if format == "markdown":
        return JSONResponse(content={"markdown": "# Decoded PDF Heading\n\nThis is placeholder text."})
    
    # Return placeholder mapping to CommonOutputSchema
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
                        "engine_used": "paddleocr"
                    }
                ]
            }
        ],
        "metadata": {
            "processed_at": datetime.datetime.utcnow().isoformat(),
            "avg_confidence": 0.95,
            "low_confidence_pages": []
        }
    }

@router.get("/{job_id}/pages/{page_number}")
def get_page_result(
    job_id: uuid.UUID,
    page_number: int,
    db: Session = Depends(get_db)
):
    """
    Retrieves layout details and preview image representing OCR bounding boxes for a single page.
    """
    # TODO: Fetch specific page data from DB.
    # Return page block geometry + rendering URL.
    return {
        "job_id": job_id,
        "page_number": page_number,
        "image_preview_url": f"http://localhost:9000/docscribe-storage/results/{job_id}/page_{page_number}.png",
        "blocks": []
    }

@router.post("/{job_id}/reprocess", response_model=JobCreateResponse)
def reprocess_job(
    job_id: uuid.UUID,
    force_engine: Optional[Literal["paddleocr", "surya"]] = Query(None, description="Force a specific engine over router logic"),
    db: Session = Depends(get_db)
):
    """
    Re-runs OCR extraction using alternate settings or forcing a specific engine path.
    """
    # TODO: Load original job, update configurations, and trigger queue.
    new_job_id = uuid.uuid4()
    return JobCreateResponse(
        job_id=new_job_id,
        document_id=uuid.uuid4(),
        status="queued",
        message="Reprocessing task enqueued successfully under new job."
    )
