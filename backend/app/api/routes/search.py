# Purpose: Full-text search across OCR'd page content via PostgreSQL's
# built-in tsvector/GIN indexing (Phase 2 item 6) — no new service.

import logging
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["Search"])

# Postgres's "simple" text search config does no stemming or stopword
# removal for any language — plain tokenization only. This means Devanagari
# (hi/mr) and Gujarati (gu) text is matched as literal whitespace/punctuation
# tokens, not linguistically normalized (no plural/inflection folding) the
# way a language-aware config would. Exact-substring-ish matching still
# works; "find all forms of this word" does not. No Postgres built-in config
# supports these scripts, so "simple" is the correct choice available today.
_TS_CONFIG = "simple"

# ts_headline() only wraps matched terms — it does not escape the rest of
# the snippet, which is raw OCR'd document text an attacker could control
# (e.g. a PDF containing literal "<script>..."). Using HTML tags as the
# match markers and rendering the result with dangerouslySetInnerHTML would
# be an XSS hole. Using non-printable, practically-never-OCR'd delimiters
# instead lets the frontend split on them and render every segment as plain
# text (escaped by React by default), applying emphasis only via a React
# element — never raw HTML.
_HEADLINE_OPTIONS = "StartSel=\x01, StopSel=\x02, MaxWords=35, MinWords=15"


class SearchResult(BaseModel):
    document_id: str
    filename: str
    job_id: str
    page_number: int
    snippet: str
    rank: float


@router.get("", response_model=List[SearchResult])
def search_pages(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Full-text search across every OCR'd page's text, ranked by
    ts_rank, with a highlighted snippet per match.
    """
    rows = db.execute(
        text(
            """
            SELECT
                d.id AS document_id,
                d.filename,
                j.id AS job_id,
                p.page_number,
                ts_headline(:config, coalesce(p.search_text, ''), plainto_tsquery(:config, :q), :headline_options) AS snippet,
                ts_rank(p.search_vector, plainto_tsquery(:config, :q)) AS rank
            FROM pages p
            JOIN jobs j ON p.job_id = j.id
            JOIN documents d ON j.document_id = d.id
            WHERE p.search_vector @@ plainto_tsquery(:config, :q)
            ORDER BY rank DESC
            LIMIT :limit
            """
        ),
        {"config": _TS_CONFIG, "q": q, "limit": limit, "headline_options": _HEADLINE_OPTIONS},
    ).fetchall()

    return [
        SearchResult(
            document_id=str(row.document_id),
            filename=row.filename,
            job_id=str(row.job_id),
            page_number=row.page_number,
            snippet=row.snippet,
            rank=float(row.rank),
        )
        for row in rows
    ]
