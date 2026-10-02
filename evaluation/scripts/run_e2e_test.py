# Purpose: End-to-end test of the live DocScribe pipeline — uploads a real PDF
# to a running docker-compose stack, waits for the Celery worker to finish
# OCR, and checks the result through the same API surface the frontend uses.
# Not a unit test (see backend/tests/ for those): this needs the full stack
# (API + worker + Postgres + Redis + MinIO) actually running.
#
# Run: pip install -r evaluation/scripts/requirements.txt
#      pytest evaluation/scripts/run_e2e_test.py -v
# Requires: docker compose up -d (from the repo root) beforehand.

import os
import time
from pathlib import Path

import pytest
import requests

API_BASE_URL = os.environ.get("DOCSCRIBE_API_URL", "http://localhost:8000/api/v1")
REAL_PDF_PATH = Path(__file__).parent.parent / "real_pdf" / "input" / "real_sample.pdf"

# Confidence floor well below typical real-document performance so this test
# catches real regressions without being flaky over minor OCR/model/
# dependency-version drift.
MIN_AVG_CONFIDENCE = 0.70

# Celery can be mid-download of a large model on a cold worker (observed:
# Surya's recognition model download alone can take minutes) — generous but
# bounded.
MAX_WAIT_SECONDS = 900
POLL_INTERVAL_SECONDS = 5


@pytest.fixture(scope="module")
def completed_job():
    """Uploads a real PDF once and waits for it to finish — shared by every
    assertion in this module so the pipeline only runs once per test session."""
    assert REAL_PDF_PATH.exists(), f"Missing test PDF: {REAL_PDF_PATH}"

    with open(REAL_PDF_PATH, "rb") as f:
        response = requests.post(
            f"{API_BASE_URL}/jobs",
            files={"file": (REAL_PDF_PATH.name, f, "application/pdf")},
            timeout=30,
        )
    assert response.status_code == 202, f"Upload failed: {response.status_code} {response.text}"
    job_id = response.json()["job_id"]

    deadline = time.time() + MAX_WAIT_SECONDS
    status_payload = None
    while time.time() < deadline:
        status_response = requests.get(f"{API_BASE_URL}/jobs/{job_id}", timeout=10)
        assert status_response.status_code == 200
        status_payload = status_response.json()
        if status_payload["status"] in ("done", "failed"):
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    assert status_payload is not None, "Job status was never retrieved"
    assert status_payload["status"] == "done", (
        f"Job did not complete successfully within {MAX_WAIT_SECONDS}s: {status_payload}"
    )
    return job_id, status_payload


def test_job_completes_with_confidence_above_floor(completed_job):
    _job_id, status_payload = completed_job
    avg_confidence = status_payload["avg_confidence"]
    assert avg_confidence is not None
    assert avg_confidence >= MIN_AVG_CONFIDENCE, (
        f"avg_confidence {avg_confidence} fell below floor {MIN_AVG_CONFIDENCE}"
    )


def test_page_has_blocks(completed_job):
    job_id, _status_payload = completed_job
    response = requests.get(f"{API_BASE_URL}/jobs/{job_id}/pages/1", timeout=10)
    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert len(blocks) > 0, "Expected at least one block on page 1"


def test_json_result_is_well_formed(completed_job):
    job_id, _status_payload = completed_job
    response = requests.get(
        f"{API_BASE_URL}/jobs/{job_id}/result", params={"format": "json"}, timeout=10
    )
    assert response.status_code == 200
    payload = response.json()
    assert "pages" in payload
    assert len(payload["pages"]) > 0
    assert len(payload["pages"][0]["blocks"]) > 0


def test_markdown_result_is_non_empty(completed_job):
    job_id, _status_payload = completed_job
    response = requests.get(
        f"{API_BASE_URL}/jobs/{job_id}/result", params={"format": "markdown"}, timeout=10
    )
    assert response.status_code == 200
    assert len(response.json()["markdown"].strip()) > 0


def test_txt_result_is_non_empty(completed_job):
    job_id, _status_payload = completed_job
    response = requests.get(
        f"{API_BASE_URL}/jobs/{job_id}/result", params={"format": "txt"}, timeout=10
    )
    assert response.status_code == 200
    txt = response.json()["txt"]
    assert len(txt.strip()) > 0
    assert "Page 1" in txt


@pytest.mark.parametrize("pdf_format", ["searchable_pdf", "highlighted_pdf", "structured_pdf"])
def test_pdf_export_is_a_valid_searchable_pdf(completed_job, pdf_format):
    job_id, _status_payload = completed_job
    response = requests.get(
        f"{API_BASE_URL}/jobs/{job_id}/result",
        params={"format": pdf_format},
        timeout=30,
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")

    import pymupdf

    doc = pymupdf.open(stream=response.content, filetype="pdf")
    try:
        assert doc.page_count > 0
        # All three variants carry a real text layer (invisible for
        # searchable_pdf/highlighted_pdf, visible for structured_pdf) —
        # PyMuPDF's own text extraction should find something in each.
        extracted_text = doc[0].get_text()
        assert len(extracted_text.strip()) > 0
    finally:
        doc.close()


def test_search_finds_uploaded_document(completed_job):
    _job_id, _status_payload = completed_job
    # The real PDF's own content varies, so search against a query built
    # from the page's own extracted text instead of a hardcoded word.
    page_response = requests.get(f"{API_BASE_URL}/jobs/{_job_id}/pages/1", timeout=10)
    blocks = page_response.json()["blocks"]
    text_blocks = [b for b in blocks if b.get("text")]
    assert text_blocks, "Expected at least one text block to build a search query from"
    query_word = text_blocks[0]["text"].split()[0]

    response = requests.get(f"{API_BASE_URL}/search", params={"q": query_word}, timeout=10)
    assert response.status_code == 200
    results = response.json()
    assert any(r["page_number"] == 1 for r in results), (
        f"Expected a search hit on page 1 for query {query_word!r}, got {results}"
    )
