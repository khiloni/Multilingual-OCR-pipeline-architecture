# Purpose: Celery task that orchestrates the full OCR pipeline:
#   download PDF → rasterize pages → preprocess → route engines → normalise →
#   persist Page+Block rows to Postgres → upload JSON/Markdown artifacts to MinIO.
# Phase 0 item 4: stubs replaced with real preprocess + PaddleOCR + routing.

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2
import fitz  # PyMuPDF — used only to count pages here; rasterization is in preprocess
import numpy as np

from app.core.db import SessionLocal
from app.models.models import Block, Document, Job, Page
from app.services.storage import storage_service
from worker.celery_app import celery_app
from worker.pipeline.engines.structure_engine import StructureEngine, find_caption
from worker.pipeline.export_pdf import create_highlighted_pdf, create_searchable_pdf, create_structured_pdf
from worker.pipeline.normalize import convert_to_markdown, convert_to_txt, normalize_to_common_schema
from worker.pipeline.postprocess import postprocess_page_blocks
from worker.pipeline.preprocess import preprocess_page, rasterize_pdf_page
from worker.pipeline.quality import compute_page_quality_score
from worker.pipeline.router import EngineRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level router/structure engine — created once per Celery worker
# process so model weights are loaded only on worker startup, not per task.
# ---------------------------------------------------------------------------
_router: Optional[EngineRouter] = None
_structure_engine: Optional[StructureEngine] = None


def _get_router() -> EngineRouter:
    global _router
    if _router is None:
        logger.info("Initialising EngineRouter for this worker process")
        _router = EngineRouter()
    return _router


def _get_structure_engine() -> StructureEngine:
    global _structure_engine
    if _structure_engine is None:
        logger.info("Initialising StructureEngine for this worker process")
        _structure_engine = StructureEngine()
    return _structure_engine


# needs_review=False only when a figure clears this confidence bar AND a
# caption was matched (ARCHITECTURE.md §10.3).
_FIGURE_REVIEW_CONFIDENCE_THRESHOLD = 0.85


def _build_figure_blocks(
    image: np.ndarray,
    figure_regions: List[Dict[str, Any]],
    text_blocks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Converts raw layout figure/chart regions into Common Output Schema
    "figure" blocks (ARCHITECTURE.md §10.3). Crops are cut from the same
    preprocessed image OCR ran on, so bbox coordinates line up exactly.

    image_url is NOT set here — the block doesn't have its final block_id
    yet (that's assigned by normalize.sort_reading_order()). The crop's PNG
    bytes are stashed under the internal "_crop_bytes" key; a post-
    normalize pass in process_ocr() uploads it to MinIO using the real
    block_id and replaces "_crop_bytes" with the real "image_url", then
    strips the internal key before persistence/serialization.
    """
    h, w = image.shape[:2]
    blocks: List[Dict[str, Any]] = []

    for region in figure_regions:
        x0, y0, x1, y1 = region["bbox"]
        xi0, yi0 = max(0, int(x0)), max(0, int(y0))
        xi1, yi1 = min(w, int(x1)), min(h, int(y1))
        if xi1 <= xi0 or yi1 <= yi0:
            logger.warning("Skipping figure region with degenerate bbox %s", region["bbox"])
            continue

        crop = image[yi0:yi1, xi0:xi1]
        ok, png_bytes = cv2.imencode(".png", crop)
        if not ok:
            logger.warning("Failed to encode figure crop as PNG, skipping region %s", region["bbox"])
            continue

        caption = find_caption(region["bbox"], text_blocks)
        confidence = region["confidence"]
        needs_review = not (confidence >= _FIGURE_REVIEW_CONFIDENCE_THRESHOLD and caption is not None)

        blocks.append({
            "type": "figure",
            "subtype": region["subtype"],
            "bbox": [float(x0), float(y0), float(x1), float(y1)],
            "confidence": confidence,
            "text": "",  # figures carry no OCR text of their own
            "language": "und",
            "engine_used": "layout",
            "caption": caption,
            "alt_text": None,
            "needs_review": needs_review,
            "_crop_bytes": png_bytes.tobytes(),
        })

    return blocks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_job_status(
    db,
    job: Job,
    status: str,
    *,
    avg_confidence: Optional[float] = None,
    avg_quality_score: Optional[float] = None,
    completed: bool = False,
) -> None:
    job.status = status
    if avg_confidence is not None:
        job.avg_confidence = avg_confidence
    if avg_quality_score is not None:
        job.avg_quality_score = avg_quality_score
    if completed:
        job.completed_at = datetime.utcnow()
    db.commit()


def _count_pdf_pages(pdf_bytes: bytes) -> int:
    """Returns the real page count from the PDF stream via PyMuPDF."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception as exc:
        logger.warning("Could not count PDF pages: %s — defaulting to 1", exc)
        return 1


def _process_single_page(
    router: EngineRouter,
    structure_engine: StructureEngine,
    pdf_bytes: bytes,
    page_index: int,
    page_number: int,
    job_id: uuid.UUID,
) -> Tuple[List[Dict[str, Any]], str, Optional[bytes]]:
    """
    Rasterises, preprocesses, and runs OCR routing for one PDF page.

    Also uploads the raw rasterized page image to
    results/{job_id}/page_{page_number}.png so GET /jobs/{id}/pages/{n} has a
    real preview to point at instead of a URL for an object that never
    existed. Upload failure is logged but never fails the page — the OCR
    result matters more than the preview image.

    Returns (blocks, ocr_attempt, image_bytes) — blocks is a list of raw
    block dicts (no engine_used / block_id yet — those are added by the
    router and normaliser respectively); ocr_attempt records which image
    variant won (router.EngineRouter.process_page — Phase 2 item 2);
    image_bytes is the raw rasterized PNG, reused by create_searchable_pdf()
    (Phase 2 item 1) so it isn't re-rasterized or re-downloaded from MinIO.

    Any exception is caught, logged, and an error sentinel block is returned
    so the failure is visible in the output without aborting the whole job
    (ARCHITECTURE.md §15 reliability requirement).
    """
    try:
        image_bytes = rasterize_pdf_page(pdf_bytes, page_index)

        try:
            storage_service.client.put_object(
                Bucket=storage_service.bucket,
                Key=f"results/{job_id}/page_{page_number}.png",
                Body=image_bytes,
                ContentType="image/png",
            )
        except Exception:
            logger.warning(
                "Failed to upload page preview image for page %d", page_number, exc_info=True
            )

        image = preprocess_page(image_bytes)
        blocks, ocr_attempt = router.process_page(image)
        logger.info(
            "Page %d: %d text blocks extracted (ocr_attempt=%s)",
            page_number, len(blocks), ocr_attempt,
        )

        # Structure detection (tables + figures/charts — Phase 1 items 4/5).
        # Runs unconditionally, unlike the confidence-gated Surya fallback —
        # a table/figure needs to be caught regardless of plain-text
        # confidence. Additive only: a failure here must never lose the
        # text blocks already extracted above (structure_engine already
        # catches its own exceptions and returns empty lists on failure).
        structure = structure_engine.analyze_page(image)
        table_blocks = [
            {**t, "text": "", "language": "und", "engine_used": "table_pipeline"}
            for t in structure["tables"]
        ]
        figure_blocks = _build_figure_blocks(image, structure["figure_regions"], blocks)
        logger.info(
            "Page %d: %d table block(s), %d figure/chart block(s)",
            page_number, len(table_blocks), len(figure_blocks),
        )

        # Post-processing (text cleanup, duplicate/overlap suppression,
        # confidence-based review routing) — see postprocess.py. Runs on
        # the full combined list so it can tell which plain-text blocks
        # fall inside a table/figure region and should be dropped as
        # duplicates rather than kept as independent content.
        return postprocess_page_blocks(blocks + table_blocks + figure_blocks), ocr_attempt, image_bytes
    except Exception as exc:
        logger.error(
            "Page %d processing failed: %s",
            page_number,
            exc,
            exc_info=True,
        )
        # Return a sentinel error block so downstream consumers know this page
        # had a problem but the job itself is not failed.
        return [
            {
                "text": f"[OCR ERROR on page {page_number}: {exc}]",
                "bbox": [0.0, 0.0, 0.0, 0.0],
                "confidence": 0.0,
                "language": "und",
                "type": "paragraph",
                "engine_used": "error",
                "review_status": "needs_review",
                "needs_review": True,
            }
        ], "error", None


def _upload_figure_crops(normalized_output: Dict[str, Any], job_id: uuid.UUID) -> None:
    """
    Uploads each figure block's stashed crop PNG to MinIO now that
    normalize_to_common_schema() has assigned real block_ids, then sets
    image_url and strips the internal "_crop_bytes" key so it never reaches
    persistence or the JSON/Markdown artifacts. Mutates normalized_output
    in place. Upload failure is logged and leaves image_url as None rather
    than failing the whole job — a missing figure image is recoverable
    (reprocess), losing the rest of the page's results is not.
    """
    for page_data in normalized_output.get("pages", []):
        for block in page_data.get("blocks", []):
            crop_bytes = block.pop("_crop_bytes", None)
            if crop_bytes is None:
                continue
            storage_path = f"results/{job_id}/{block['block_id']}.png"
            try:
                storage_service.client.put_object(
                    Bucket=storage_service.bucket,
                    Key=storage_path,
                    Body=crop_bytes,
                    ContentType="image/png",
                )
                block["image_url"] = f"minio://{storage_service.bucket}/{storage_path}"
            except Exception:
                logger.warning(
                    "Failed to upload figure crop for block_id=%s", block["block_id"], exc_info=True
                )


def _compute_quality_scores(
    normalized_output: Dict[str, Any], page_images: Dict[int, bytes]
) -> float:
    """
    Computes the authoritative per-page OCR quality score (Phase 2 item 5)
    now that correction + structure detection have both run, mutating each
    page dict in place with "quality_score". Unlike router.py's early
    retry-gate score (raw text blocks only, before correction), this one
    sees the complete picture: corrected text, tables, and figures.

    Returns the document-level average (mean of per-page scores), or 0.0 if
    there are no pages.
    """
    scores = []
    for page_data in normalized_output.get("pages", []):
        page_number = page_data["page_number"]
        image_bytes = page_images.get(page_number)
        if image_bytes:
            nparr = np.frombuffer(image_bytes, dtype=np.uint8)
            decoded = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            height, width = decoded.shape[:2] if decoded is not None else (0, 0)
        else:
            height, width = 0, 0

        score = compute_page_quality_score(page_data.get("blocks", []), width, height)
        page_data["quality_score"] = round(score, 4)
        scores.append(score)

    return sum(scores) / len(scores) if scores else 0.0


def _persist_pages_and_blocks(
    db,
    job: Job,
    normalized_output: Dict[str, Any],
) -> None:
    """
    Writes Page and Block ORM rows to Postgres for the completed job.

    Matches the DDL in ARCHITECTURE.md §11:
        JOBS → PAGES → BLOCKS
    """
    for page_data in normalized_output.get("pages", []):
        page_row = Page(
            id=uuid.uuid4(),
            job_id=job.id,
            page_number=page_data["page_number"],
            languages_detected=",".join(page_data.get("language_detected", [])),
            ocr_attempt=page_data.get("ocr_attempt"),
            quality_score=page_data.get("quality_score"),
            search_text=" ".join(
                b.get("text", "") for b in page_data.get("blocks", []) if b.get("text")
            ),
        )
        db.add(page_row)
        db.flush()  # populate page_row.id before referencing it in Block rows

        for block_data in page_data.get("blocks", []):
            block_type = block_data.get("type", "paragraph")
            block_row = Block(
                id=uuid.uuid4(),
                page_id=page_row.id,
                type=block_type,
                content=block_data.get("text", ""),
                bbox=block_data.get("bbox", [0.0, 0.0, 0.0, 0.0]),
                confidence=block_data.get("confidence", 0.0),
                language=block_data.get("language", "und"),
                engine_used=block_data.get("engine_used", "paddleocr"),
                subtype=block_data.get("subtype"),
                table_data=block_data.get("table") if block_type == "table" else None,
                image_url=block_data.get("image_url") if block_type == "figure" else None,
                caption=block_data.get("caption") if block_type == "figure" else None,
                needs_review=block_data.get("needs_review"),
                review_status=block_data.get("review_status"),
                original_text=block_data.get("original_text"),
                correction_applied=block_data.get("correction_applied"),
                correction_reason=block_data.get("correction_reason"),
            )
            db.add(block_row)

    db.commit()
    logger.info("Persisted pages + blocks to DB for job_id=%s", job.id)


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(name="worker.tasks.process_ocr", bind=True)
def process_ocr(self, job_id_str: str, document_id_str: str) -> Dict[str, Any]:
    """
    Background Celery task that runs the full OCR pipeline for a document:

    1. Downloads raw PDF from MinIO.
    2. Counts real page count and updates the Document record if needed.
    3. For each page:
       a. Rasterizes at 200 DPI via PyMuPDF.
       b. Deskews + denoises via OpenCV.
       c. Routes through PaddleOCR → confidence check → optional Surya fallback.
    4. Normalizes all page outputs to the Common Output Schema (ARCHITECTURE.md §10).
    5. Persists Page + Block rows to Postgres.
    6. Uploads structured JSON and Markdown to MinIO.
    7. Marks job as done (or failed on unrecoverable error).
    """
    logger.info(
        "Starting OCR task | job_id=%s document_id=%s",
        job_id_str,
        document_id_str,
    )
    job_id = uuid.UUID(job_id_str)
    doc_id = uuid.UUID(document_id_str)

    self.update_state(state="PROGRESS", meta={"percent": 5, "status": "Initialising"})

    db = SessionLocal()
    try:
        # ---- Load DB records --------------------------------------------
        job = db.query(Job).filter(Job.id == job_id).first()
        document = db.query(Document).filter(Document.id == doc_id).first()
        if job is None or document is None:
            raise ValueError(
                f"Job or Document not found (job_id={job_id}, document_id={doc_id})"
            )

        _set_job_status(db, job, "processing")
        self.update_state(
            state="PROGRESS", meta={"percent": 10, "status": "Downloading PDF"}
        )

        # ---- Download PDF from MinIO ------------------------------------
        pdf_bytes = storage_service.download_document(document.storage_path)
        logger.info(
            "Downloaded %d bytes from %s", len(pdf_bytes), document.storage_path
        )

        # ---- Resolve real page count ------------------------------------
        real_page_count = _count_pdf_pages(pdf_bytes)
        if document.page_count != real_page_count:
            logger.info(
                "Correcting page_count %d → %d for document_id=%s",
                document.page_count,
                real_page_count,
                doc_id,
            )
            document.page_count = real_page_count
            db.commit()

        # ---- Process each page ------------------------------------------
        router = _get_router()
        structure_engine = _get_structure_engine()
        raw_pages: Dict[int, List[Dict[str, Any]]] = {}
        page_ocr_attempts: Dict[int, str] = {}
        page_images: Dict[int, bytes] = {}

        self.update_state(
            state="PROGRESS",
            meta={"percent": 20, "status": "Starting page processing"},
        )

        for page_index in range(real_page_count):
            page_number = page_index + 1  # 1-based for the output schema
            progress_pct = 20 + int(60 * page_index / real_page_count)
            self.update_state(
                state="PROGRESS",
                meta={
                    "percent": progress_pct,
                    "status": f"Processing page {page_number}/{real_page_count}",
                },
            )

            blocks, ocr_attempt, image_bytes = _process_single_page(
                router, structure_engine, pdf_bytes, page_index, page_number, job_id
            )
            raw_pages[page_number] = blocks
            if image_bytes is not None:
                page_images[page_number] = image_bytes
            page_ocr_attempts[page_number] = ocr_attempt

        # ---- Normalise --------------------------------------------------
        self.update_state(
            state="PROGRESS", meta={"percent": 82, "status": "Normalising output schema"}
        )
        normalized_output = normalize_to_common_schema(
            doc_id, document.filename, raw_pages, page_ocr_attempts=page_ocr_attempts
        )
        _upload_figure_crops(normalized_output, job_id)
        avg_quality_score = _compute_quality_scores(normalized_output, page_images)
        markdown_output = convert_to_markdown(normalized_output)
        txt_output = convert_to_txt(normalized_output)

        # ---- Persist Page + Block rows to Postgres ----------------------
        self.update_state(
            state="PROGRESS", meta={"percent": 88, "status": "Persisting results to DB"}
        )
        _persist_pages_and_blocks(db, job, normalized_output)

        # ---- Upload artifacts to MinIO ----------------------------------
        self.update_state(
            state="PROGRESS", meta={"percent": 93, "status": "Uploading artifacts"}
        )
        storage_service.upload_result(
            job_id, json.dumps(normalized_output, ensure_ascii=False, indent=2), "json"
        )
        storage_service.upload_result(job_id, markdown_output, "markdown")
        storage_service.upload_result(job_id, txt_output, "txt")

        try:
            searchable_pdf_bytes = create_searchable_pdf(normalized_output, page_images)
            storage_service.upload_result_binary(
                job_id, searchable_pdf_bytes, "result_searchable.pdf", "application/pdf"
            )
        except Exception:
            logger.warning(
                "Failed to generate/upload searchable PDF for job_id=%s — "
                "other results are unaffected",
                job_id, exc_info=True,
            )

        try:
            highlighted_pdf_bytes = create_highlighted_pdf(normalized_output, page_images)
            storage_service.upload_result_binary(
                job_id, highlighted_pdf_bytes, "result_highlighted.pdf", "application/pdf"
            )
        except Exception:
            logger.warning(
                "Failed to generate/upload highlighted PDF for job_id=%s — "
                "other results are unaffected",
                job_id, exc_info=True,
            )

        try:
            def _fetch_crop(image_url: str) -> bytes:
                # "minio://bucket/results/.../p1_b5.png" -> "results/.../p1_b5.png"
                storage_path = image_url.split("/", 3)[3]
                return storage_service.download_document(storage_path)

            structured_pdf_bytes = create_structured_pdf(
                normalized_output, page_images, fetch_crop=_fetch_crop
            )
            storage_service.upload_result_binary(
                job_id, structured_pdf_bytes, "result_structured.pdf", "application/pdf"
            )
        except Exception:
            logger.warning(
                "Failed to generate/upload structured PDF for job_id=%s — "
                "other results are unaffected",
                job_id, exc_info=True,
            )

        # ---- Mark job done ----------------------------------------------
        avg_conf = float(normalized_output["metadata"]["avg_confidence"])
        _set_job_status(
            db, job, "done",
            avg_confidence=avg_conf, avg_quality_score=avg_quality_score, completed=True,
        )

        self.update_state(
            state="SUCCESS", meta={"percent": 100, "status": "Completed"}
        )
        logger.info(
            "OCR task complete | job_id=%s avg_confidence=%.4f pages=%d",
            job_id_str,
            avg_conf,
            real_page_count,
        )
        return {
            "status": "success",
            "job_id": job_id_str,
            "avg_confidence": avg_conf,
            "page_count": real_page_count,
        }

    except Exception as exc:
        logger.exception("OCR task failed | job_id=%s error=%s", job_id_str, exc)
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job is not None:
                job.error_message = str(exc)
                _set_job_status(db, job, "failed", completed=True)
        except Exception:
            logger.exception(
                "Failed to mark job %s as failed during exception handler", job_id_str
            )
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise

    finally:
        db.close()
