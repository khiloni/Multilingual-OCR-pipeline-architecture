# Purpose: Real PaddleOCR (PP-OCRv6) inference engine with quad→bbox conversion,
# per-block language detection, and block-type heuristics.
# Maps raw PaddleOCR 3.x OCRResult output to the Common Output Schema defined in ARCHITECTURE.md §10.
#
# PaddleOCR 3.x API changes vs 2.x:
#   - Constructor args: no use_angle_cls / use_gpu / show_log.  Use lang=, plus the
#     enable_mkldnn=False kwarg (passed through to PaddleX) to disable MKL-DNN.
#     Without enable_mkldnn=False the paddle_static runner enables oneDNN which triggers
#     a "ConvertPirAttribute2RuntimeAttribute not supported" crash in PaddlePaddle 3.3.x on CPU.
#   - Inference: ocr() is deprecated; use predict().  Returns list[OCRResult] per input image.
#   - OCRResult format (dict-like, iterable over keys):
#       result["rec_texts"]  – list[str]           – one recognised string per text line
#       result["rec_scores"] – list[float]          – confidence in [0, 1] per text line
#       result["rec_polys"]  – list[ndarray(4,2)]  – 4-point quads, dtype int16
#       result["rec_boxes"]  – ndarray(N,4) int16  – axis-aligned [x0,y0,x1,y1] per line
#
# MODEL SELECTION (this revision):
#   Previously used lang="ch", which PaddleOCR 3.7+ silently resolves to the
#   PP-OCRv6_medium pipeline anyway. We now name the det/rec models explicitly.
#   This is functionally identical today, but it gives us a single, obvious
#   place to point at a fine-tuned recognition checkpoint later (see
#   PADDLEOCR_REC_MODEL_DIR below) without touching constructor logic again.

import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model selection (tunable via env var so a fine-tuned checkpoint can be
# swapped in without a code change / rebuild — just set the env var in
# docker-compose.yml or .env and restart the worker container)
# ---------------------------------------------------------------------------
_DET_MODEL_NAME = "PP-OCRv6_medium_det"
_REC_MODEL_NAME = "PP-OCRv6_medium_rec"

# If set, points PaddleOCR at a local fine-tuned recognition model directory
# (the output of `tools/export_model.py` / PaddleX export mode) instead of
# downloading the stock PP-OCRv6_medium_rec weights.
# VERIFY the exact constructor kwarg name against your installed paddleocr
# version before relying on this — PaddleOCR's 3.x constructor args have
# already changed across minor versions once (the show_log removal). Check
# `python -c "from paddleocr import PaddleOCR; help(PaddleOCR.__init__)"`
# for the current param name (expected: text_recognition_model_dir) before
# wiring a fine-tuned model in.
_CUSTOM_REC_MODEL_DIR = os.environ.get("PADDLEOCR_REC_MODEL_DIR")  # None until fine-tune is exported

# ---------------------------------------------------------------------------
# Block-type heuristics (tunable)
# ---------------------------------------------------------------------------
# A text line is classified as a "heading" when it is short AND the bounding
# box height-to-width ratio suggests it is large/prominent.  These thresholds
# work well for typical document scans; adjust per corpus if needed.
_HEADING_MAX_WORDS: int = 8
_HEADING_MIN_BBOX_HEIGHT: int = 30   # pixels at 200 DPI
_PARAGRAPH_MIN_WORDS: int = 5

# ISO 639-1 codes DocScribe validates against (Phase 0 target scope).
# PP-OCRv6 still recognizes all 50 of its languages at the character level —
# this whitelist only governs what the language-validation layer is willing
# to *claim* a block is. Anything langdetect ranks outside this set falls
# back to "und" rather than being silently mislabeled.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({
    "en",  # English
    "hi",  # Hindi
    "gu",  # Gujarati
    "mr",  # Marathi
    "ta",  # Tamil
    "te",  # Telugu
    "bn",  # Bengali
    "kn",  # Kannada
})


class PaddleOCREngine:
    """
    Primary OCR execution engine using PaddleOCR PP-OCRv6 (version 3.x).

    PaddleOCR is lazily initialised on first use so the Celery worker process
    can import this module without triggering a heavyweight model load at
    startup.  The first call to extract_text() pays the ~3-5 s init cost;
    subsequent calls within the same worker process are fast.
    """

    def __init__(self) -> None:
        self._ocr = None  # lazy — populated on first extract_text() call
        logger.info("PaddleOCREngine created (model will load on first inference call)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_text(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        Runs PaddleOCR text detection + recognition on a preprocessed page image.

        Args:
            image: BGR np.ndarray — output of preprocess.preprocess_page().

        Returns:
            List of block dicts matching the Common Output Schema (ARCHITECTURE.md §10):
            [
                {
                    "text":       str,
                    "bbox":       [x0, y0, x1, y1],   # axis-aligned, float pixels
                    "confidence": float,               # 0.0–1.0
                    "language":   str,                 # ISO 639-1 or "und"
                    "type":       str,                 # heading | paragraph | caption
                }
            ]
            Empty list if PaddleOCR finds no text on the page.
        """
        ocr = self._get_ocr()
        logger.info("Running PaddleOCR inference on image shape=%s", image.shape)

        try:
            # PaddleOCR 3.x: ocr() is deprecated; predict() is the correct API.
            # Returns list[OCRResult] — one OCRResult per input image.
            raw_results = ocr.predict(image)
        except Exception as exc:
            logger.error("PaddleOCR.predict() raised an exception: %s", exc, exc_info=True)
            return []

        if not raw_results:
            logger.info("PaddleOCR returned no results for this page")
            return []

        # We always pass a single image so raw_results has exactly one entry.
        result = raw_results[0]

        return self._parse_ocr_result(result)

    def extract_tables(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        PP-Structure table extraction — deferred to Phase 1.

        Kept as a stub returning [] so the router can call this without
        branching on engine capability.  When PP-Structure is wired in Phase 1,
        this method will return structured table dicts and the router will
        automatically begin triggering the Surya fallback path for table pages.
        """
        logger.debug("extract_tables: PP-Structure not yet wired (Phase 1) — returning []")
        return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_ocr(self):
        """
        Lazy-initialise the PaddleOCR client.  Thread-safe for single-threaded
        Celery workers; if concurrency > 1 the first few concurrent calls may
        each create their own instance (harmless but slightly wasteful).
        """
        if self._ocr is None:
            try:
                from paddleocr import PaddleOCR  # type: ignore[import]
            except ImportError as exc:
                raise RuntimeError(
                    "paddleocr is not installed. "
                    "Add 'paddlepaddle' and 'paddleocr' to requirements.txt and rebuild."
                ) from exc

            kwargs: Dict[str, Any] = dict(
                text_detection_model_name=_DET_MODEL_NAME,
                text_recognition_model_name=_REC_MODEL_NAME,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
            )

            if _CUSTOM_REC_MODEL_DIR:
                # Fine-tuned checkpoint present — point PaddleOCR at it instead
                # of downloading the stock PP-OCRv6_medium_rec weights.
                logger.info(
                    "Loading fine-tuned recognition model from PADDLEOCR_REC_MODEL_DIR=%s",
                    _CUSTOM_REC_MODEL_DIR,
                )
                kwargs["text_recognition_model_dir"] = _CUSTOM_REC_MODEL_DIR
            else:
                logger.info(
                    "Initialising PaddleOCR 3.x (det=%s, rec=%s, enable_mkldnn=False) — "
                    "model weights will download on first run if not cached",
                    _DET_MODEL_NAME, _REC_MODEL_NAME,
                )

            # PaddleOCR 3.x constructor — all 2.x args have been replaced:
            #
            #   text_detection_model_name / text_recognition_model_name
            #                                 Explicit model selection (PP-OCRv6_medium tier).
            #                                 Equivalent to lang="ch" today, but gives us a
            #                                 named hook for swapping in fine-tuned weights.
            #
            #   use_doc_orientation_classify=False
            #                                 Skip page-level rotation classifier;
            #                                 deskew in preprocess.py already handles this.
            #
            #   use_doc_unwarping=False       Skip document unwarping (UVDoc model);
            #                                 not needed for flat scanned pages.
            #
            #   use_textline_orientation=False
            #                                 Skip per-line orientation classifier;
            #                                 preprocess deskew covers this.
            #
            #   enable_mkldnn=False           CRITICAL — disables MKL-DNN (oneDNN).
            #                                 With PaddlePaddle 3.3.x on CPU the paddle_static
            #                                 runner calls config.enable_new_ir(True) by default.
            #                                 When MKL-DNN is also active this triggers:
            #                                   "ConvertPirAttribute2RuntimeAttribute not
            #                                    supported [pir::ArrayAttribute<pir::DoubleAttribute>]"
            #                                 at inference time in onednn_instruction.cc.
            #                                 Passing enable_mkldnn=False forces run_mode="paddle"
            #                                 (plain static executor) which avoids the crash.
            self._ocr = PaddleOCR(**kwargs)
            logger.info("PaddleOCR initialised successfully")
        return self._ocr

    def _parse_ocr_result(self, result: Any) -> List[Dict[str, Any]]:
        """
        Converts a PaddleOCR 3.x OCRResult into a list of Common Output Schema blocks.

        PaddleOCR 3.x OCRResult is a dict-like object (iterable over keys) with:
            result["rec_texts"]  – list[str]           one recognised string per text line
            result["rec_scores"] – list[float]         confidence in [0, 1] per line
            result["rec_polys"]  – list[ndarray(4,2)]  4-point quads, dtype int16
            result["rec_boxes"]  – ndarray(N,4) int16  axis-aligned [x0,y0,x1,y1] per line

        NOTE: This is entirely different from the 2.x format, which returned a list of
        [[[x0,y0],...], (text, confidence)] pairs.  Do not mix the two parsers.
        """
        try:
            rec_texts: List[str] = result["rec_texts"]
            rec_scores: List[float] = result["rec_scores"]
            rec_polys = result["rec_polys"]   # list of ndarray(4, 2)
        except (KeyError, TypeError) as exc:
            logger.warning(
                "OCRResult missing expected keys (rec_texts/rec_scores/rec_polys): %s — "
                "returning empty block list",
                exc,
            )
            return []

        if not rec_texts:
            logger.info("PaddleOCR returned no text lines for this page")
            return []

        if len(rec_texts) != len(rec_scores) or len(rec_texts) != len(rec_polys):
            logger.warning(
                "PaddleOCR result length mismatch: texts=%d scores=%d polys=%d — "
                "truncating to shortest",
                len(rec_texts), len(rec_scores), len(rec_polys),
            )

        blocks: List[Dict[str, Any]] = []
        for text, score, poly in zip(rec_texts, rec_scores, rec_polys):
            block = self._parse_text_line(text, float(score), poly)
            if block is not None:
                blocks.append(block)

        logger.info("PaddleOCR extracted %d text blocks from page", len(blocks))
        return blocks

    def _parse_text_line(
        self,
        text: str,
        confidence: float,
        poly: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        Converts a single (text, confidence, quad_polygon) triple into a
        Common Output Schema block dict.

        Args:
            text:       Recognised string (may be empty or whitespace).
            confidence: Recognition confidence in [0, 1].
            poly:       ndarray of shape (4, 2) with (x, y) corner coordinates.

        Returns:
            Block dict, or None if text is empty/whitespace or the polygon is malformed.
        """
        text = str(text).strip()
        if not text:
            return None

        # Convert 4-point polygon → axis-aligned bbox [x0, y0, x1, y1]
        try:
            pts = np.asarray(poly, dtype=float)   # shape (4, 2)
            xs = pts[:, 0]
            ys = pts[:, 1]
            bbox = [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]
        except Exception as exc:
            logger.warning("Skipping line with malformed polygon %r: %s", poly, exc)
            return None

        # Language detection — langdetect can raise on very short strings
        lang = self._detect_language(text)

        # Block-type heuristic
        block_type = self._infer_block_type(text, bbox)

        return {
            "text": text,
            "bbox": bbox,
            "confidence": confidence,
            "language": lang,
            "type": block_type,
        }

    @staticmethod
    def _detect_language(text: str) -> str:
        """
        Detects the dominant language of a text block using langdetect,
        restricted to SUPPORTED_LANGUAGES.

        Falls back to "und" (undetermined) when:
          - the string is too short/symbol-heavy for reliable detection, or
          - none of langdetect's ranked candidates fall inside SUPPORTED_LANGUAGES.

        Uses detect_langs() (ranked candidates) rather than detect() (top-1 only)
        so a block isn't discarded just because langdetect's single best guess
        happens to be a language outside scope, when a lower-ranked in-scope
        guess is still plausible.

        KNOWN LIMITATION: Hindi ("hi") and Marathi ("mr") share the Devanagari
        script. langdetect separates them on vocabulary/n-grams, which is less
        reliable on short OCR lines than on full sentences — expect occasional
        cross-labeling between the two. Document this as a stated limitation
        of the "language-correct" claim rather than treating it as solved.
        """
        if len(text.strip()) < 4:
            return "und"
        try:
            from langdetect import detect_langs  # type: ignore[import]
            candidates = detect_langs(text)  # ranked by probability, descending
        except Exception:
            # LangDetectException or ImportError — silently degrade
            return "und"

        for candidate in candidates:
            if candidate.lang in SUPPORTED_LANGUAGES:
                return candidate.lang
        return "und"

    @staticmethod
    def _infer_block_type(text: str, bbox: List[float]) -> str:
        """
        Applies lightweight heuristics to assign a semantic block type.

        Rules (evaluated in order):
        1. Short text + tall bounding box → "heading"
        2. Longer text → "paragraph"
        3. Default fall-through → "paragraph"

        These heuristics are intentionally simple for Phase 0 and will be
        replaced by PP-Structure layout classification in Phase 1.
        """
        words = text.split()
        bbox_height = bbox[3] - bbox[1]  # y1 - y0

        if len(words) <= _HEADING_MAX_WORDS and bbox_height >= _HEADING_MIN_BBOX_HEIGHT:
            return "heading"
        return "paragraph"
