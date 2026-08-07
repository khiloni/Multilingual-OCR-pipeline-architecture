# Purpose: API route endpoints for listing previously uploaded documents and deleting resources.
# Future TODOs: Add search filters (by name or date range), bulk deletes, and pagination controls.

import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.schemas.schemas import DocumentListResponse
from app.services.storage import storage_service

router = APIRouter(prefix="/documents", tags=["Documents"])

@router.get("", response_model=List[DocumentListResponse])
def list_documents(
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    Lists uploaded documents (paginated).
    """
    # TODO: Query database Document table with limit and offset.
    return []

@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    """
    Removes database records and corresponding files from the MinIO storage.
    """
    # TODO: Look up doc in DB to find storage path
    # storage_service.delete_document(doc.storage_path)
    # db.delete(doc)
    # db.commit()
    return
