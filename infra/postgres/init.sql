-- Purpose: Initialize the database tables (Documents, Jobs, Pages, Blocks) based on the ER Diagram in Section 11 of ARCHITECTURE.md.
-- Future TODOs: Create indexes on foreign key fields, enable cascade delete rules, and set up schema migration tooling.

-- Create UUID extension if not exists (for Postgres UUID support)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create Table: documents
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filename VARCHAR(255) NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    page_count INTEGER NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Create Table: jobs
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'queued',
    avg_confidence REAL,
    avg_quality_score REAL,
    error_message TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

-- Create Table: pages
CREATE TABLE IF NOT EXISTS pages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    languages_detected VARCHAR(255),
    ocr_attempt VARCHAR(20),
    quality_score REAL,
    search_text TEXT,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(search_text, ''))) STORED
);

CREATE INDEX IF NOT EXISTS idx_pages_search_vector ON pages USING GIN(search_vector);

-- Create Table: blocks
CREATE TABLE IF NOT EXISTS blocks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    content TEXT,
    bbox JSON NOT NULL,
    confidence REAL NOT NULL,
    language VARCHAR(10),
    engine_used VARCHAR(50) NOT NULL,
    -- Sparse, populated only for their relevant block `type` (table/figure).
    subtype VARCHAR(20),
    table_data JSON,
    image_url VARCHAR(512),
    caption TEXT,
    needs_review BOOLEAN,
    -- Populated for every block type by the post-processing stage:
    -- "accepted" | "flagged" | "needs_review", from the block's own confidence.
    review_status VARCHAR(20),
    -- API-based correction (item 0b). `content` holds the (possibly
    -- corrected) text; original_text preserves the raw OCR text so both
    -- are recoverable — content is never overwritten without a trace.
    original_text TEXT,
    correction_applied BOOLEAN,
    correction_reason VARCHAR(40)
);

-- Indexes for performance queries
CREATE INDEX IF NOT EXISTS idx_jobs_document_id ON jobs(document_id);
CREATE INDEX IF NOT EXISTS idx_pages_job_id ON pages(job_id);
CREATE INDEX IF NOT EXISTS idx_blocks_page_id ON blocks(page_id);
