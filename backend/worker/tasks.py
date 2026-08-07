# Purpose: Define the Celery task runner executing PDF page split, preprocessing, routing, and normalization.
# Future TODOs: Add error retries, handle real PyMuPDF split rendering, and push notifications to Postgres/Webhooks.

import logging
import uuid
from typing import Dict, List, Any
from worker.celery_app import celery_app
from worker.pipeline.preprocess import preprocess_page
from worker.pipeline.router import router
from worker.pipeline.normalize import normalize_to_common_schema, convert_to_markdown
from app.services.storage import storage_service

logger = logging.getLogger(__name__)

@celery_app.task(name="worker.tasks.process_ocr", bind=True)
def process_ocr(self, job_id_str: str, document_id_str: str) -> Dict[str, Any]:
    """
    Background Celery task that processes a document:
      1. Downloads raw PDF from Object Storage.
      2. Rasterizes pages into images.
      3. For each page:
         - Preprocesses (deskew / denoise).
         - Routes to engine (PaddleOCR / Surya VLM fallback).
      4. Normalizes outputs to JSON Common Schema.
      5. Generates Markdown representation.
      6. Uploads final artifacts to MinIO.
      7. Updates database job status to done/failed.
    """
    logger.info(f"Starting Celery OCR task for job_id={job_id_str}, document_id={document_id_str}")
    job_id = uuid.UUID(job_id_str)
    doc_id = uuid.UUID(document_id_str)

    # Update state: PROCESSING
    self.update_state(state="PROGRESS", meta={"percent": 10, "status": "Downloading document"})

    # TODO: db_session = SessionLocal()
    # TODO: Update job.status = "processing"

    try:
        # 1. Fetch raw PDF file
        # mock path or check DB for path
        storage_path = f"documents/{doc_id}/sample.pdf"
        pdf_bytes = storage_service.download_document(storage_path)

        self.update_state(state="PROGRESS", meta={"percent": 30, "status": "Rasterizing PDF pages"})
        # 2. Rasterize pages (Mocking 1 page for skeleton execution)
        # TODO: Use fitz.open(stream=pdf_bytes) to loop pages.
        page_count = 1
        raw_pages: Dict[int, List[Dict[str, Any]]] = {}

        for p_idx in range(1, page_count + 1):
            self.update_state(state="PROGRESS", meta={"percent": 50, "status": f"Processing page {p_idx}"})
            
            # Mock raw image bytes
            mock_image_bytes = b"dummy image raw pixels"
            
            # 3. Preprocess
            # denoised_img = preprocess_page(mock_image_bytes)
            # For skeleton, let's pass a dummy numpy array to represent the decoded image
            dummy_image = np_array_placeholder = [] # np.zeros((100, 100, 3), dtype=np.uint8)
            
            # 4. Engine routing
            # blocks = router.process_page(dummy_image)
            blocks = [
                {
                    "text": "This is a paragraph of extracted text.",
                    "bbox": [10.0, 15.0, 300.0, 45.0],
                    "confidence": 0.94,
                    "language": "en",
                    "type": "paragraph"
                }
            ]
            raw_pages[p_idx] = blocks

        self.update_state(state="PROGRESS", meta={"percent": 80, "status": "Normalizing schemas"})
        # 5. Normalize
        normalized_output = normalize_to_common_schema(doc_id, "sample.pdf", raw_pages)

        # 6. Convert to Markdown
        markdown_output = convert_to_markdown(normalized_output)

        # 7. Upload final artifacts
        storage_service.upload_result(job_id, str(normalized_output), "json")
        storage_service.upload_result(job_id, markdown_output, "markdown")

        # TODO: Save blocks to DB
        # TODO: Update job.status = "done", job.avg_confidence = avg_conf

        self.update_state(state="SUCCESS", meta={"status": "Completed OCR pipeline processing"})
        return {"status": "success", "job_id": job_id_str, "avg_confidence": normalized_output["metadata"]["avg_confidence"]}

    except Exception as e:
        logger.exception(f"OCR Task failed for job_id={job_id_str}")
        # TODO: Update job.status = "failed"
        self.update_state(state="FAILURE", meta={"error": str(e)})
        raise e
