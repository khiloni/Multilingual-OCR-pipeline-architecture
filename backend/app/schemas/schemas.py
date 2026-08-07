# Purpose: Pydantic schemas representing request payloads, response bodies, and the Common Output Schema.
# Future TODOs: Add stricter validator rules for bounding boxes (e.g. must be 4 elements, >=0) and type mappings.

from datetime import datetime
from typing import List, Optional, Literal
from uuid import UUID
from pydantic import BaseModel, Field

# --- API Response/Request Schemas ---

class JobCreateResponse(BaseModel):
    """
    Response schema when submitting a new document processing job.
    """
    job_id: UUID
    document_id: UUID
    status: str
    message: str

class JobStatusResponse(BaseModel):
    """
    Response schema when checking status of a job.
    """
    job_id: UUID
    document_id: UUID
    status: Literal["queued", "processing", "done", "failed"]
    avg_confidence: Optional[float] = None
    started_at: datetime
    completed_at: Optional[datetime] = None

class DocumentListResponse(BaseModel):
    """
    Response schema for listing uploaded documents.
    """
    id: UUID
    filename: str
    storage_path: str
    page_count: int
    uploaded_at: datetime

    class Config:
        from_attributes = True

# --- Common Output Schema (Section 10 of ARCHITECTURE.md) ---

class BlockSchema(BaseModel):
    """
    Normalized layout block content details.
    """
    block_id: str
    type: Literal["heading", "paragraph", "table", "list", "caption"]
    text: str
    bbox: List[float] = Field(..., min_length=4, max_length=4, description="[x0, y0, x1, y1] coordinates")
    confidence: float
    language: Optional[str] = None
    engine_used: Literal["paddleocr", "surya"]

class PageSchema(BaseModel):
    """
    Normalized single-page OCR block contents.
    """
    page_number: int
    language_detected: List[str]
    blocks: List[BlockSchema]

class DocumentMetadataSchema(BaseModel):
    """
    Summary metrics and details for a processed job.
    """
    processed_at: datetime
    avg_confidence: float
    low_confidence_pages: List[int]

class CommonOutputSchema(BaseModel):
    """
    The unified schema representing complete OCR extraction results for a document.
    """
    document_id: UUID
    filename: str
    page_count: int
    pages: List[PageSchema]
    metadata: DocumentMetadataSchema
