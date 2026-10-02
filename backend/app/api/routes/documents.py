# Purpose: API route endpoints for listing previously uploaded documents and deleting resources.
# Future TODOs: Add search filters (by name or date range), bulk deletes, and pagination controls.

import logging
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.models.models import Document
from app.schemas.schemas import DocumentListResponse
from app.services.storage import storage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])

@router.get("", response_model=List[DocumentListResponse])
def list_documents(
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    Lists uploaded documents (paginated), newest first. Each entry includes
    its LATEST job's status/confidence/error (a document can have several
    jobs — reprocess creates a new one under the same document_id) so the
    frontend document list can render a status badge in one request.
    """
    docs = (
        db.query(Document)
        .order_by(Document.uploaded_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    results: List[DocumentListResponse] = []
    for doc in docs:
        latest_job = max(doc.jobs, key=lambda j: j.started_at, default=None) if doc.jobs else None
        results.append(DocumentListResponse(
            id=doc.id,
            filename=doc.filename,
            storage_path=doc.storage_path,
            page_count=doc.page_count,
            uploaded_at=doc.uploaded_at,
            latest_job_id=latest_job.id if latest_job else None,
            latest_status=latest_job.status if latest_job else None,
            avg_confidence=latest_job.avg_confidence if latest_job else None,
            avg_quality_score=latest_job.avg_quality_score if latest_job else None,
            error_message=latest_job.error_message if latest_job else None,
        ))
    return results

@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    """
    Removes database records (documents → jobs → pages → blocks cascade) and
    corresponding files from MinIO (original PDF + all job result artifacts).
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found.")

    storage_service.delete_document(doc.storage_path)
    for job in doc.jobs:
        deleted = storage_service.delete_prefix(f"results/{job.id}/")
        logger.info("Deleted %d result object(s) for job_id=%s", deleted, job.id)

    db.delete(doc)
    db.commit()
    return
