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
    Lists uploaded documents (paginated), newest first.
    """
    return (
        db.query(Document)
        .order_by(Document.uploaded_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

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
