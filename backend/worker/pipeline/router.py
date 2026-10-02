# Purpose: Engine selection routing logic — runs PaddleOCR, evaluates confidence /
# layout / script-mix triggers (ARCHITECTURE.md §9), and falls back to Surya when needed.
# Surya inference remains stubbed in Phase 0; the routing decision itself is real.

import logging
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Tuple

import numpy as np

from app.core.config import settings
from worker.pipeline.engines.paddle_engine import PaddleOCREngine
from worker.pipeline.engines.surya_engine import SuryaOCREngine
from worker.pipeline.preprocess import adaptive_threshold_variant, enhance_contrast_clahe
from worker.pipeline.quality import compute_page_quality_score

# Image-variant retry (Phase 2 item 2) — tried in this order, same engine,
# before falling back to Surya. Each entry: (ocr_attempt label, image
# transform). Capped by settings.IMAGE_VARIANT_MAX_ATTEMPTS so latency
# stays bounded regardless of how many variants are listed here.
_IMAGE_VARIANTS: List[Tuple[str, Any]] = [
    ("enhanced", enhance_contrast_clahe),
    ("threshold", adaptive_threshold_variant),
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unicode script-range table
# Each entry: (range_start, range_end_inclusive, script_family_name)
# Ranges chosen to cover the scripts targeted by ARCHITECTURE.md §2 / §16.
# ---------------------------------------------------------------------------
_SCRIPT_RANGES: List[Tuple[int, int, str]] = [
    (0x0000, 0x024F, "latin"),          # Basic Latin + Latin Extended A/B
    (0x0370, 0x03FF, "greek"),
    (0x0400, 0x04FF, "cyrillic"),
    (0x0600, 0x06FF, "arabic"),
    (0x0900, 0x097F, "devanagari"),     # Hindi, Marathi, Sanskrit, …
    (0x0980, 0x09FF, "bengali"),
    (0x0A00, 0x0A7F, "gurmukhi"),
    (0x0A80, 0x0AFF, "gujarati"),
    (0x0B00, 0x0B7F, "oriya"),
    (0x0B80, 0x0BFF, "tamil"),
    (0x0C00, 0x0C7F, "telugu"),
    (0x0C80, 0x0CFF, "kannada"),
    (0x0D00, 0x0D7F, "malayalam"),
    (0x4E00, 0x9FFF, "cjk"),            # CJK Unified Ideographs
    (0xAC00, 0xD7AF, "hangul"),
    (0x3040, 0x30FF, "japanese"),       # Hiragana + Katakana
]

# A script is considered "present" on a page when it accounts for at least
# this fraction of all classifiable characters across all blocks.
_SCRIPT_MIN_SHARE: float = 0.10

# Minimum number of distinct script families that triggers the mixed-script flag.
_MIXED_SCRIPT_MIN_FAMILIES: int = 2

# Script families the fine-tuned primary recognition model covers
# (English, Hindi, Marathi, Gujarati). Mixing these with each other does not
# trigger the Surya fallback — only a script outside this set does. Sending
# every multilingual page to Surya cost minutes per page (model load plus CPU
# inference) for pages the primary model already reads confidently.
_PRIMARY_MODEL_SCRIPTS = frozenset({"latin", "devanagari", "gujarati"})

# IOU threshold for considering two bboxes as "covering the same region"
# during block merging.
_IOU_MERGE_THRESHOLD: float = 0.30


# ---------------------------------------------------------------------------
# EngineRouter
# ---------------------------------------------------------------------------

class EngineRouter:
    """
    Implements the routing flow described in ARCHITECTURE.md §9:

        Page image
          → PaddleOCR (fast path)
          → evaluate: avg_confidence < threshold  OR  table detected  OR  mixed script?
              Yes → Surya fallback  → merge best-of-both
              No  → accept PaddleOCR output directly
          → return block list (engine_used field set on every block)
    """

    def __init__(self) -> None:
        self.paddle = PaddleOCREngine()
        self.surya = SuryaOCREngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_page(self, image: np.ndarray) -> Tuple[List[Dict[str, Any]], str]:
        """
        Runs the full routing pipeline for a single page image.

        Args:
            image: Preprocessed BGR np.ndarray (output of preprocess_page()).

        Returns:
            (blocks, ocr_attempt) — blocks is a list of normalised block
            dicts (text, bbox, confidence, language, type, engine_used);
            ocr_attempt records which image variant produced the accepted
            result: "original" | "enhanced" | "threshold" | "surya".
        """
        # ---- 1. Run PaddleOCR (always — fast path) ----------------------
        paddle_blocks = self.paddle.extract_text(image)
        paddle_tables = self.paddle.extract_tables(image)

        # ---- 2. Evaluate routing triggers --------------------------------
        avg_confidence = _compute_avg_confidence(paddle_blocks)
        has_table = len(paddle_tables) > 0
        is_mixed_script = _detect_mixed_script(paddle_blocks)

        page_height, page_width = image.shape[:2]
        # Computed on raw text blocks only, before correction/structure
        # detection run — text_quality and page_coverage are therefore
        # approximations (no correction_applied signal yet, no table/figure
        # area yet). Good enough as an early retry/fallback gate; tasks.py
        # recomputes the authoritative per-page score after the full
        # pipeline (correction + structure detection) has run.
        quality_score = compute_page_quality_score(paddle_blocks, page_width, page_height)

        threshold = settings.PADDLE_CONFIDENCE_THRESHOLD
        quality_threshold = settings.QUALITY_SCORE_THRESHOLD
        ocr_attempt = "original"

        routing_decision = {
            "avg_confidence": round(avg_confidence, 4),
            "threshold": threshold,
            "quality_score": round(quality_score, 4),
            "quality_threshold": quality_threshold,
            "has_table": has_table,
            "is_mixed_script": is_mixed_script,
            "paddle_block_count": len(paddle_blocks),
        }

        # ---- 2b. Image-variant retry (item 2) — same engine, different
        # preprocessing, tried BEFORE the (slower, different-engine) Surya
        # fallback. Only runs when confidence or quality is the actual
        # problem — a table or a script the primary model doesn't cover
        # won't be fixed by contrast/threshold tweaks, so don't waste the
        # retry budget.
        if (
            (avg_confidence < threshold or quality_score < quality_threshold)
            and not has_table
            and not is_mixed_script
            and settings.ENABLE_IMAGE_VARIANT_RETRY
        ):
            best_blocks, best_confidence, best_attempt = paddle_blocks, avg_confidence, ocr_attempt
            for name, transform in _IMAGE_VARIANTS[: settings.IMAGE_VARIANT_MAX_ATTEMPTS]:
                try:
                    variant_image = transform(image)
                    variant_blocks = self.paddle.extract_text(variant_image)
                except Exception as exc:
                    logger.warning("Image-variant retry '%s' failed, skipping: %s", name, exc, exc_info=True)
                    continue
                variant_confidence = _compute_avg_confidence(variant_blocks)
                logger.info(
                    "Image-variant retry '%s': avg_confidence=%.4f (best so far: %.4f from '%s')",
                    name, variant_confidence, best_confidence, best_attempt,
                )
                if variant_confidence > best_confidence:
                    best_blocks, best_confidence, best_attempt = variant_blocks, variant_confidence, name
                if best_confidence >= threshold:
                    break  # good enough — stop spending retry budget

            paddle_blocks, avg_confidence, ocr_attempt = best_blocks, best_confidence, best_attempt
            quality_score = compute_page_quality_score(paddle_blocks, page_width, page_height)
            routing_decision["avg_confidence"] = round(avg_confidence, 4)
            routing_decision["quality_score"] = round(quality_score, 4)
            routing_decision["winning_attempt"] = ocr_attempt

        # ---- 3a. Fast path: accept PaddleOCR output ----------------------
        if (
            avg_confidence >= threshold
            and quality_score >= quality_threshold
            and not has_table
            and not is_mixed_script
        ):
            logger.info(
                "Routing decision: engine=paddleocr (%s) | %s",
                ocr_attempt, routing_decision,
            )
            for block in paddle_blocks:
                block["engine_used"] = "paddleocr"
            return paddle_blocks, ocr_attempt

        # ---- 3b. Fallback path: trigger Surya ----------------------------
        trigger_reasons = []
        if avg_confidence < threshold:
            trigger_reasons.append(f"low_confidence({avg_confidence:.3f}<{threshold})")
        if quality_score < quality_threshold:
            trigger_reasons.append(f"low_quality_score({quality_score:.3f}<{quality_threshold})")
        if has_table:
            trigger_reasons.append("table_detected")
        if is_mixed_script:
            trigger_reasons.append("mixed_script")

        logger.warning(
            "Routing decision: engine=surya | triggers=%s | %s",
            trigger_reasons,
            routing_decision,
        )

        # Surya is a *soft* fallback — any crash (model init failure,
        # transformers version mismatch, inference error) must NEVER
        # discard the real Paddle results that were already computed.
        # If Surya fails we log the error, stamp all Paddle blocks as
        # engine_used="paddleocr", and return them as-is.
        try:
            surya_blocks = self.surya.extract_text_and_layout(image)
        except Exception as surya_exc:
            logger.error(
                "Surya fallback failed — using Paddle output as-is. Error: %s",
                surya_exc,
                exc_info=True,
            )
            for block in paddle_blocks:
                block["engine_used"] = "paddleocr"
            return paddle_blocks, ocr_attempt

        for block in surya_blocks:
            block["engine_used"] = "surya"

        # ---- 4. Merge best-of-both by block-level confidence/overlap -----
        merged = self._merge_blocks(paddle_blocks, surya_blocks)
        return merged, "surya"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _merge_blocks(
        self,
        paddle_blocks: List[Dict[str, Any]],
        surya_blocks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Combines PaddleOCR and Surya block lists by spatial overlap.

        Algorithm (ARCHITECTURE.md §9 "merge best-of-both by block confidence"):
        - For each Surya block, find any overlapping Paddle block (IOU > threshold).
        - If an overlapping Paddle block has HIGHER confidence → prefer Paddle.
        - Otherwise keep the Surya block.
        - Paddle blocks with no matching Surya block are added as-is.

        This strategy trusts Surya's layout detection but allows Paddle to
        win individual blocks where its recognition confidence is stronger.
        """
        if not surya_blocks:
            # Fallback to Paddle if Surya returned nothing
            logger.debug("merge_blocks: Surya returned no blocks — using Paddle output")
            for b in paddle_blocks:
                b["engine_used"] = "paddleocr"
            return paddle_blocks

        if not paddle_blocks:
            return surya_blocks

        merged: List[Dict[str, Any]] = []
        paddle_matched = set()  # indices of Paddle blocks already consumed

        for s_block in surya_blocks:
            best_paddle_idx: int = -1
            best_iou: float = 0.0

            for p_idx, p_block in enumerate(paddle_blocks):
                if p_idx in paddle_matched:
                    continue
                iou = _bbox_iou(s_block["bbox"], p_block["bbox"])
                if iou > best_iou:
                    best_iou = iou
                    best_paddle_idx = p_idx

            if best_paddle_idx >= 0 and best_iou >= _IOU_MERGE_THRESHOLD:
                p_block = paddle_blocks[best_paddle_idx]
                if p_block["confidence"] > s_block["confidence"]:
                    # Paddle wins this region
                    winner = dict(p_block)
                    winner["engine_used"] = "paddleocr"
                    merged.append(winner)
                else:
                    merged.append(s_block)
                paddle_matched.add(best_paddle_idx)
            else:
                # No overlapping Paddle block — keep Surya's unique coverage
                merged.append(s_block)

        # Append any Paddle blocks not matched by a Surya block
        for p_idx, p_block in enumerate(paddle_blocks):
            if p_idx not in paddle_matched:
                extra = dict(p_block)
                extra["engine_used"] = "paddleocr"
                merged.append(extra)

        logger.info(
            "merge_blocks: surya=%d paddle=%d → merged=%d blocks",
            len(surya_blocks),
            len(paddle_blocks),
            len(merged),
        )
        return merged


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions — no engine state)
# ---------------------------------------------------------------------------

def _compute_avg_confidence(blocks: List[Dict[str, Any]]) -> float:
    """Returns the mean confidence across all blocks, or 0.0 for empty input."""
    if not blocks:
        return 0.0
    confs = [b.get("confidence", 0.0) for b in blocks]
    return float(np.mean(confs))


def _detect_mixed_script(blocks: List[Dict[str, Any]]) -> bool:
    """
    Returns True when 2+ distinct Unicode script families each contribute
    at least _SCRIPT_MIN_SHARE of the total classifiable characters across
    all blocks on the page.

    Digits, punctuation, and whitespace are not counted (they appear in all
    scripts and would otherwise inflate the Latin share).
    """
    script_counts: Counter = Counter()
    total_chars = 0

    for block in blocks:
        text = block.get("text", "")
        for ch in text:
            cp = ord(ch)
            # Skip non-letter characters (digits, punctuation, spaces)
            if not unicodedata.category(ch).startswith("L"):
                continue
            total_chars += 1
            for start, end, name in _SCRIPT_RANGES:
                if start <= cp <= end:
                    script_counts[name] += 1
                    break

    if total_chars == 0:
        return False

    families_above = {
        name for name, count in script_counts.items()
        if count / total_chars >= _SCRIPT_MIN_SHARE
    }
    families_above_threshold = len(families_above)
    # Only a mix that includes a script the primary model wasn't trained on
    # is a routing problem — a page that mixes only supported scripts (e.g.
    # English + Hindi + Gujarati) is the normal case, not a fallback trigger.
    is_mixed = (
        families_above_threshold >= _MIXED_SCRIPT_MIN_FAMILIES
        and bool(families_above - _PRIMARY_MODEL_SCRIPTS)
    )
    if is_mixed:
        logger.info(
            "_detect_mixed_script: %d script families detected above %.0f%% share: %s",
            families_above_threshold,
            _SCRIPT_MIN_SHARE * 100,
            dict(script_counts.most_common(5)),
        )
    return is_mixed


def _bbox_iou(bbox_a: List[float], bbox_b: List[float]) -> float:
    """
    Computes Intersection-over-Union for two axis-aligned bboxes.

    Both bboxes are [x0, y0, x1, y1].  Returns 0.0 if either bbox is
    degenerate (zero area) or there is no overlap.
    """
    ax0, ay0, ax1, ay1 = bbox_a
    bx0, by0, bx1, by1 = bbox_b

    inter_x0 = max(ax0, bx0)
    inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1)
    inter_y1 = min(ay1, by1)

    inter_w = max(0.0, inter_x1 - inter_x0)
    inter_h = max(0.0, inter_y1 - inter_y0)
    inter_area = inter_w * inter_h

    if inter_area == 0.0:
        return 0.0

    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union_area = area_a + area_b - inter_area

    return inter_area / union_area if union_area > 0 else 0.0
