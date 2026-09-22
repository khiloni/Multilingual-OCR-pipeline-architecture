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
    error_message: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None

class DocumentListResponse(BaseModel):
    """
    Response schema for listing uploaded documents. Includes the LATEST
    job's status/confidence/error so the frontend document list (Phase 1
    item 8) can render a status badge without an extra request per row —
    a document can have multiple jobs (reprocess creates a new one under
    the same document_id), so "latest" means most recently started.
    """
    id: UUID
    filename: str
    storage_path: str
    page_count: int
    uploaded_at: datetime
    latest_job_id: Optional[UUID] = None
    latest_status: Optional[str] = None
    avg_confidence: Optional[float] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True

# --- Common Output Schema (Section 10 of ARCHITECTURE.md) ---

class TableCellSchema(BaseModel):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    text: str
    is_header: bool = False
    confidence: float

class TableSchema(BaseModel):
    rows: int
    cols: int
    cells: List[TableCellSchema]

class BlockSchema(BaseModel):
    """
    Normalized layout block content details. Not currently used as a
    response_model anywhere (GET /jobs/{id}/result returns the stored JSON
    directly) — kept here as documentation of the Common Output Schema
    (ARCHITECTURE.md §10). Fields are Optional per block `type`: text
    blocks populate text/language/engine_used; table blocks populate
    `table`; figure blocks populate subtype/image_url/caption/needs_review.
    """
    block_id: str
    type: Literal["heading", "paragraph", "table", "list", "caption", "figure"]
    text: Optional[str] = None
    bbox: List[float] = Field(..., min_length=4, max_length=4, description="[x0, y0, x1, y1] coordinates")
    confidence: float
    language: Optional[str] = None
    engine_used: Optional[Literal["paddleocr", "surya", "table_pipeline", "layout", "error"]] = None
    table: Optional[TableSchema] = None
    subtype: Optional[Literal["image", "chart"]] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None
    alt_text: Optional[str] = None
    needs_review: Optional[bool] = None
    review_status: Optional[Literal["accepted", "flagged", "needs_review"]] = None

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
