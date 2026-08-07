# Purpose: SQLAlchemy ORM models for Documents, Jobs, Pages, and Blocks to persist OCR pipeline data.
# Future TODOs: Add indexes on foreign keys, enable cascade deletes, and implement schema versioning (Alembic).

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.db import Base

class Document(Base):
    """
    Represents an uploaded multi-page document (PDF) to be processed.
    """
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(255), nullable=False)
    storage_path = Column(String(512), nullable=False)
    page_count = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    jobs = relationship("Job", back_populates="document", cascade="all, delete-orphan")

class Job(Base):
    """
    Represents an OCR processing job associated with a document.
    """
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(50), nullable=False, default="queued")  # queued, processing, done, failed
    avg_confidence = Column(Float, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    document = relationship("Document", back_populates="jobs")
    pages = relationship("Page", back_populates="job", cascade="all, delete-orphan")

class Page(Base):
    """
    Represents a single rasterized page within an OCR job.
    """
    __tablename__ = "pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    page_number = Column(Integer, nullable=False)
    languages_detected = Column(String(255), nullable=True)  # Comma-separated or JSON array of lang codes

    # Relationships
    job = relationship("Job", back_populates="pages")
    blocks = relationship("Block", back_populates="page", cascade="all, delete-orphan")

class Block(Base):
    """
    Represents an extracted layout block (paragraph, table, header, etc.) on a page.
    """
    __tablename__ = "blocks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(50), nullable=False)  # heading, paragraph, table, list, caption
    content = Column(Text, nullable=True)
    bbox = Column(JSON, nullable=False)  # [x0, y0, x1, y1] coordinates
    confidence = Column(Float, nullable=False)
    language = Column(String(10), nullable=True)
    engine_used = Column(String(50), nullable=False)  # paddleocr, surya, etc.

    # Relationships
    page = relationship("Page", back_populates="blocks")
