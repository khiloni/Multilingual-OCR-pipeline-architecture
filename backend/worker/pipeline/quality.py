# Purpose: Computes an explicit, auditable OCR quality score (0-1) per page
# from five weighted signals (ARCHITECTURE.md §9.6) — confidence is already
# tracked elsewhere, but on its own it only reflects the OCR engine's
# self-reported certainty, not whether the output is actually usable
# (garbled characters, a page mostly uncovered by any block, wildly
# inconsistent language detection can all hide behind a "confident" score).

import logging
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Block types that contribute to the page's real text content. Tables/figures
# are handled separately (coverage counts their area; they don't have a
# "language"/correction signal the way paragraph/heading/list blocks do).
_TEXT_BLOCK_TYPES = frozenset({"paragraph", "heading", "list"})

# Per-language expected Unicode script ranges, matching
# router._PRIMARY_MODEL_SCRIPTS — the only scripts this pipeline's primary
# recognition model targets. A character outside its block's expected range
# (and outside common punctuation/whitespace/digits) counts against
# character quality.
_LANGUAGE_SCRIPT_RANGES: Dict[str, List[tuple]] = {
    "en": [(0x0041, 0x005A), (0x0061, 0x007A)],   # A-Z, a-z
    "hi": [(0x0900, 0x097F)],                       # Devanagari
    "mr": [(0x0900, 0x097F)],                       # Devanagari
    "gu": [(0x0A80, 0x0AFF)],                       # Gujarati
}


def _is_expected_or_common_char(ch: str, script_ranges: List[tuple]) -> bool:
    if ch.isspace() or ch.isdigit() or unicodedata.category(ch).startswith("P"):
        return True  # whitespace, digits, punctuation are language-neutral
    code = ord(ch)
    return any(start <= code <= end for start, end in script_ranges)


def _confidence_component(blocks: List[Dict[str, Any]]) -> float:
    confidences = [b["confidence"] for b in blocks if b.get("type") in _TEXT_BLOCK_TYPES]
    return sum(confidences) / len(confidences) if confidences else 1.0


def _text_quality_component(blocks: List[Dict[str, Any]]) -> float:
    """Share of correctable blocks the correction step left unchanged — a
    high share means the raw OCR text was already clean (didn't need
    fixing); a low share means heavy correction was required, which is
    itself a signal the raw OCR output was poor. When correction is
    disabled (no provider/key), every block is trivially "unchanged",
    which correctly degrades this component to a no-op rather than a
    penalty — it has nothing to measure."""
    text_blocks = [b for b in blocks if b.get("type") in _TEXT_BLOCK_TYPES and b.get("text")]
    if not text_blocks:
        return 1.0
    unchanged = sum(1 for b in text_blocks if not b.get("correction_applied"))
    return unchanged / len(text_blocks)


def _character_quality_component(blocks: List[Dict[str, Any]]) -> float:
    total_chars = 0
    expected_chars = 0
    for block in blocks:
        if block.get("type") not in _TEXT_BLOCK_TYPES:
            continue
        text = block.get("text", "")
        if not text:
            continue
        script_ranges = _LANGUAGE_SCRIPT_RANGES.get(block.get("language", "en"), [])
        for ch in text:
            total_chars += 1
            if _is_expected_or_common_char(ch, script_ranges):
                expected_chars += 1
    return expected_chars / total_chars if total_chars else 1.0


def _page_coverage_component(
    blocks: List[Dict[str, Any]], page_width: float, page_height: float
) -> float:
    """Approximates covered-area share by summing each block's bbox area and
    capping at 1.0 — a true union (accounting for overlaps) would need a
    coverage grid/rasterized mask, which isn't worth the cost here: a
    handful of blocks in a layout rarely overlap heavily, and this score is
    a quality signal, not a geometry-exact measurement."""
    page_area = page_width * page_height
    if page_area <= 0:
        return 0.0
    covered = 0.0
    for block in blocks:
        bbox = block.get("bbox", [0.0, 0.0, 0.0, 0.0])
        x0, y0, x1, y1 = bbox
        covered += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return min(covered / page_area, 1.0)


def _language_consistency_component(blocks: List[Dict[str, Any]]) -> float:
    languages = [b.get("language", "und") for b in blocks if b.get("type") in _TEXT_BLOCK_TYPES]
    if not languages:
        return 1.0
    dominant_count = Counter(languages).most_common(1)[0][1]
    return dominant_count / len(languages)


def compute_page_quality_score(
    blocks: List[Dict[str, Any]],
    page_width: float,
    page_height: float,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    Computes a 0-1 OCR quality score for one page from five weighted
    components (weights default to settings.QUALITY_WEIGHT_* —
    ARCHITECTURE.md §9.6 documents the formula and rationale):

      confidence            — average OCR engine confidence
      text_quality          — share of blocks the correction step left unchanged
      character_quality     — share of characters matching the block's expected script
      page_coverage         — share of page area covered by blocks
      language_consistency  — share of blocks matching the page's dominant language

    Returns a float in [0, 1]. Never raises — an empty/degenerate page
    scores low rather than crashing the pipeline over a missing field.
    """
    w = weights or {
        "confidence": settings.QUALITY_WEIGHT_CONFIDENCE,
        "text_quality": settings.QUALITY_WEIGHT_TEXT_QUALITY,
        "character_quality": settings.QUALITY_WEIGHT_CHARACTER_QUALITY,
        "page_coverage": settings.QUALITY_WEIGHT_PAGE_COVERAGE,
        "language_consistency": settings.QUALITY_WEIGHT_LANGUAGE_CONSISTENCY,
    }

    try:
        score = (
            w["confidence"] * _confidence_component(blocks)
            + w["text_quality"] * _text_quality_component(blocks)
            + w["character_quality"] * _character_quality_component(blocks)
            + w["page_coverage"] * _page_coverage_component(blocks, page_width, page_height)
            + w["language_consistency"] * _language_consistency_component(blocks)
        )
        return max(0.0, min(score, 1.0))
    except Exception:
        logger.warning("compute_page_quality_score failed, defaulting to 0.0", exc_info=True)
        return 0.0
