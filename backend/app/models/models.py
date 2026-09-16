# Purpose: SQLAlchemy ORM models for Documents, Jobs, Pages, and Blocks to persist OCR pipeline data.
# Aligned exactly with infra/postgres/init.sql (types, nullability, FKs, indexes, cascades).

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import REAL, UUID
from sqlalchemy.orm import relationship

from app.core.db import Base


class Document(Base):
    """
    Represents an uploaded multi-page document (PDF) to be processed.
    Maps to: documents (id, filename, storage_path, page_count, uploaded_at)
    """

    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(255), nullable=False)
    storage_path = Column(String(512), nullable=False)
    page_count = Column(Integer, nullable=False)
    uploaded_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    jobs = relationship("Job", back_populates="document", cascade="all, delete-orphan")


class Job(Base):
    """
    Represents an OCR processing job associated with a document.
    Maps to: jobs (id, document_id, status, avg_confidence, started_at, completed_at)
    """

    __tablename__ = "jobs"
    __table_args__ = (Index("idx_jobs_document_id", "document_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(String(50), nullable=False, default="queued", server_default=text("'queued'"))
    avg_confidence = Column(REAL, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    completed_at = Column(DateTime, nullable=True)

    document = relationship("Document", back_populates="jobs")
    pages = relationship("Page", back_populates="job", cascade="all, delete-orphan")


class Page(Base):
    """
    Represents a single rasterized page within an OCR job.
    Maps to: pages (id, job_id, page_number, languages_detected)
    """

    __tablename__ = "pages"
    __table_args__ = (Index("idx_pages_job_id", "job_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number = Column(Integer, nullable=False)
    languages_detected = Column(String(255), nullable=True)

    job = relationship("Job", back_populates="pages")
    blocks = relationship("Block", back_populates="page", cascade="all, delete-orphan")


class Block(Base):
    """
    Represents an extracted layout block (paragraph, table, header, etc.) on a page.
    Maps to: blocks (id, page_id, type, content, bbox, confidence, language, engine_used)
    """

    __tablename__ = "blocks"
    __table_args__ = (Index("idx_blocks_page_id", "page_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    type = Column(String(50), nullable=False)
    content = Column(Text, nullable=True)
    bbox = Column(JSON, nullable=False)
    confidence = Column(REAL, nullable=False)
    language = Column(String(10), nullable=True)
    engine_used = Column(String(50), nullable=False)
    # Sparse columns, populated only for their relevant block `type` — see
    # ARCHITECTURE.md §11 note. Added for Phase 1 items 4/5 (table/figure
    # detection); table_data mirrors the JSON `table` field exactly.
    subtype = Column(String(20), nullable=True)
    table_data = Column(JSON, nullable=True)
    image_url = Column(String(512), nullable=True)
    caption = Column(Text, nullable=True)
    needs_review = Column(Boolean, nullable=True)

    page = relationship("Page", back_populates="blocks")
