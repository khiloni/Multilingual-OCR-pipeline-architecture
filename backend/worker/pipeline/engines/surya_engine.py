# Purpose: Real Surya OCR fallback engine — layout-aware detection + recognition,
# used by EngineRouter for low-confidence, table, or mixed-script pages
# (ARCHITECTURE.md §9). Converts Surya's own text-line output (its own bboxes,
# not PaddleOCR's) into the Common Output Schema (ARCHITECTURE.md §10).
#
# Pinned to surya-ocr==0.17.1 (see backend/requirements.txt) — this is the last
# release using the plain-PyTorch FoundationPredictor/RecognitionPredictor/
# DetectionPredictor API. 0.20.0+ replaced it with a SuryaInferenceManager that
# requires a separate vllm (NVIDIA GPU) or llama.cpp inference server, which is
# a heavier deployment than this CPU-first Docker Compose worker should take on.
#
# Router contract: when Surya fires, EngineRouter._merge_blocks() uses THESE
# bboxes for the region, not PaddleOCR's — Surya's detection model is what
# actually helps with multi-column/table/rotated layouts, so substituting only
# the recognized text into Paddle's (possibly wrong) boxes would defeat the
# point of routing to Surya at all.

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from worker.pipeline.engines.paddle_engine import PaddleOCREngine

logger = logging.getLogger(__name__)


class SuryaOCREngine:
    """
    Fallback OCR execution engine using Surya's layout-aware detection +
    recognition models.

    Models are lazily loaded on first inference call (same pattern as
    PaddleOCREngine) so importing this module / starting the Celery worker
    doesn't pay the weight-download-and-load cost until a page actually needs
    the fallback path.
    """

    def __init__(self) -> None:
        self._foundation = None
        self._recognition = None
        self._detection = None
        logger.info("SuryaOCREngine created (models load lazily on first fallback call)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_text_and_layout(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        Runs Surya detection + recognition on a preprocessed page image.

        Args:
            image: Preprocessed BGR np.ndarray (output of preprocess_page()).

        Returns:
            List of block dicts matching the Common Output Schema:
            [{"text", "bbox", "confidence", "language", "type"}, ...]
            Empty list if Surya finds no text or inference fails.
        """
        recognition_predictor, detection_predictor = self._get_predictors()

        pil_image = self._to_pil(image)
        logger.info("Running Surya inference on image size=%s", pil_image.size)

        try:
            predictions = recognition_predictor([pil_image], det_predictor=detection_predictor)
        except Exception as exc:
            logger.error("Surya recognition_predictor raised an exception: %s", exc, exc_info=True)
            return []

        if not predictions:
            logger.info("Surya returned no predictions for this page")
            return []

        return self._parse_result(predictions[0])

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_predictors(self):
        """Lazy-initialise Surya's foundation/recognition/detection predictors."""
        if self._recognition is None or self._detection is None:
            try:
                from surya.foundation import FoundationPredictor
                from surya.recognition import RecognitionPredictor
                from surya.detection import DetectionPredictor
            except ImportError as exc:
                raise RuntimeError(
                    "surya-ocr is not installed or the installed version doesn't "
                    "expose FoundationPredictor/RecognitionPredictor/DetectionPredictor. "
                    "This engine requires surya-ocr==0.17.1 specifically — see "
                    "requirements.txt / the module docstring for why."
                ) from exc

            logger.info(
                "Loading Surya foundation/recognition/detection models "
                "(weights download to the HF cache on first run if not present)"
            )
            try:
                self._foundation = FoundationPredictor()
                self._recognition = RecognitionPredictor(self._foundation)
                self._detection = DetectionPredictor()
            except Exception as exc:
                # Reset state so next call retries rather than returning
                # half-initialised predictors.
                self._foundation = None
                self._recognition = None
                self._detection = None
                raise RuntimeError(
                    f"Surya model instantiation failed: {exc}. "
                    "This is likely a transformers version mismatch — "
                    "ensure transformers>=4.37.0,<5.0.0 is installed "
                    "(see requirements.txt)."
                ) from exc

            logger.info("Surya models initialised successfully")
        return self._recognition, self._detection

    @staticmethod
    def _to_pil(image: np.ndarray):
        from PIL import Image

        # preprocess_page() emits BGR (OpenCV convention); Surya/PIL expect RGB.
        return Image.fromarray(image[:, :, ::-1])

    def _parse_result(self, result: Any) -> List[Dict[str, Any]]:
        """
        Converts one Surya OCRResult (attribute-style pydantic model in 0.17.x,
        but handled defensively as dict-like too) into Common Output Schema blocks.
        """
        text_lines = getattr(result, "text_lines", None)
        if text_lines is None and isinstance(result, dict):
            text_lines = result.get("text_lines")

        if not text_lines:
            logger.info("Surya returned no text lines for this page")
            return []

        blocks: List[Dict[str, Any]] = []
        for line in text_lines:
            block = self._parse_text_line(line)
            if block is not None:
                blocks.append(block)

        logger.info("Surya extracted %d text blocks from page", len(blocks))
        return blocks

    @staticmethod
    def _parse_text_line(line: Any) -> Optional[Dict[str, Any]]:
        """
        Converts a single Surya TextLine (or dict) into a Common Output Schema
        block, reusing PaddleOCREngine's language-detection and block-type
        heuristics so both engines classify blocks the same way.
        """
        if isinstance(line, dict):
            text = line.get("text")
            confidence = line.get("confidence")
            bbox = line.get("bbox")
        else:
            text = getattr(line, "text", None)
            confidence = getattr(line, "confidence", None)
            bbox = getattr(line, "bbox", None)

        text = (text or "").strip()
        if not text or bbox is None:
            return None

        try:
            bbox = [float(v) for v in bbox]
        except (TypeError, ValueError):
            logger.warning("Skipping Surya line with malformed bbox %r", bbox)
            return None

        confidence = float(confidence) if confidence is not None else 0.0
        lang = PaddleOCREngine._detect_language(text)
        block_type = PaddleOCREngine._infer_block_type(text, bbox)

        return {
            "text": text,
            "bbox": bbox,
            "confidence": confidence,
            "language": lang,
            "type": block_type,
        }
