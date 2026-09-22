# Purpose: Post-processing stage between raw engine output and normalize.py's
# reading-order/schema assembly. Runs once per page on the combined block
# list (text blocks from the Paddle/Surya pass + table blocks + figure
# blocks) produced by tasks._process_single_page().
#
# Split out from normalize.py deliberately: normalize.py owns reading order,
# spell-check, and final schema shape; this module owns block-level cleanup
# and quality routing, so each stays legible on its own.
#
# Responsibilities:
#   1. Text cleanup — Unicode/whitespace/control-character hygiene. Pure
#      hygiene, not semantic correction (spellcheck.py already owns that,
#      downstream of this stage).
#   2. Duplicate suppression — the dominant real-world case, confirmed
#      against a real 2-page multilingual+table+chart document: the main
#      text pass runs over the WHOLE page independently of table/figure
#      detection, so a table's cell text and a chart's axis labels/legend
#      also show up as ordinary heading/paragraph blocks with bboxes that
#      fall inside the table/figure region. Those are removed — the
#      table's own cell data already has that text with real structure,
#      and chart-internal text is noise DocScribe never promises to
#      extract (ARCHITECTURE.md §9.3: no chart data-value extraction).
#   3. General overlap/duplicate merge — a defensive, lower-priority rule
#      for same-type text blocks that overlap heavily and read as near-
#      identical text (e.g. an accidental double-detection), independent
#      of the table/figure case above.
#   4. Confidence-based review routing — every surviving block gets a
#      `review_status` tier from its own confidence, and a `needs_review`
#      boolean derived from it (figures additionally keep their existing,
#      stricter figure-specific rule — this only ever adds needs_review
#      cases for figures, never removes one).

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

_STRUCTURE_TYPES = ("table", "figure")

# ---------------------------------------------------------------------------
# 1. Text cleanup
# ---------------------------------------------------------------------------

# C0/C1 control characters minus the ones whitespace-collapsing already
# handles (\t \n \r \f \v) — those are matched by _WHITESPACE_RUN_RE instead
# so they collapse to a single space rather than vanishing entirely.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WHITESPACE_RUN_RE = re.compile(r"\s+")
_REPLACEMENT_CHAR = "�"  # U+FFFD, emitted on undecodable byte sequences


def clean_text(text: str) -> str:
    """
    Unicode-normalizes and whitespace-hygienes OCR text. Deliberately
    conservative — this is cleanup, not correction: no rewording, no
    punctuation guessing, nothing that could change meaning. Semantic
    fixes stay spellcheck.py's job, which runs after this stage.
    """
    if not text:
        return text
    # NFC matters most for Devanagari/Gujarati: OCR/engine output can mix
    # precomposed and decomposed combining-mark sequences that render
    # identically but compare unequal — normalizing avoids that.
    text = unicodedata.normalize("NFC", text)
    text = text.replace(_REPLACEMENT_CHAR, "")
    text = _CONTROL_CHAR_RE.sub("", text)
    text = _WHITESPACE_RUN_RE.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# bbox helpers
# ---------------------------------------------------------------------------

def _bbox_area(bbox: List[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_intersection_area(a: List[float], b: List[float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)


def _containment_ratio(inner: List[float], outer: List[float]) -> float:
    """Fraction of `inner`'s own area that overlaps `outer`."""
    inner_area = _bbox_area(inner)
    if inner_area <= 0:
        return 0.0
    return _bbox_intersection_area(inner, outer) / inner_area


def _iou(a: List[float], b: List[float]) -> float:
    inter = _bbox_intersection_area(a, b)
    union = _bbox_area(a) + _bbox_area(b) - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# 2. Suppress text blocks duplicated inside a table/figure region
# ---------------------------------------------------------------------------

# A text block with at least this fraction of its own area inside a
# table/figure bbox is treated as duplicate content, not independent text.
# Real table-cell/chart-noise fragments sit essentially entirely inside the
# structure's bbox (ratio ~1.0 in testing); 0.6 leaves headroom for bbox
# rounding without being loose enough to also catch genuinely separate,
# merely-adjacent text (e.g. a caption that touches the structure's edge).
_CONTAINMENT_THRESHOLD = 0.6


def suppress_blocks_inside_structures(
    blocks: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Removes non-structure blocks whose bbox is substantially contained
    within a table or figure block's bbox on the same page. Table/figure
    blocks are always kept; a block with no bbox is kept (can't test it).
    """
    structure_bboxes = [b["bbox"] for b in blocks if b.get("type") in _STRUCTURE_TYPES and b.get("bbox")]
    if not structure_bboxes:
        return blocks, 0

    kept: List[Dict[str, Any]] = []
    removed = 0
    for block in blocks:
        if block.get("type") in _STRUCTURE_TYPES:
            kept.append(block)
            continue
        bbox = block.get("bbox")
        if not bbox:
            kept.append(block)
            continue
        if any(_containment_ratio(bbox, sb) >= _CONTAINMENT_THRESHOLD for sb in structure_bboxes):
            removed += 1
            continue
        kept.append(block)
    return kept, removed


# ---------------------------------------------------------------------------
# 3. General overlap/duplicate merge (defensive, same-type text blocks only)
# ---------------------------------------------------------------------------

_DUPLICATE_IOU_THRESHOLD = 0.5
_DUPLICATE_TEXT_SIMILARITY = 0.85


def merge_duplicate_text_blocks(
    blocks: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Clusters same-type text blocks that heavily overlap AND read as
    near-identical text, keeping only the highest-confidence block per
    cluster. Table/figure blocks are never touched. This is a defensive
    catch-all independent of suppress_blocks_inside_structures() above —
    it has nothing to do with table/figure containment, only with two
    blocks that are themselves duplicates of each other.
    """
    n = len(blocks)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in range(n):
        a = blocks[i]
        if a.get("type") in _STRUCTURE_TYPES or not a.get("bbox"):
            continue
        for j in range(i + 1, n):
            b = blocks[j]
            if b.get("type") != a.get("type") or not b.get("bbox"):
                continue
            if _iou(a["bbox"], b["bbox"]) < _DUPLICATE_IOU_THRESHOLD:
                continue
            similarity = difflib.SequenceMatcher(
                None, (a.get("text") or "").strip(), (b.get("text") or "").strip()
            ).ratio()
            if similarity >= _DUPLICATE_TEXT_SIMILARITY:
                union(i, j)

    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    removed = 0
    keep_indices = set()
    for indices in clusters.values():
        if len(indices) == 1:
            keep_indices.add(indices[0])
            continue
        best = max(indices, key=lambda k: blocks[k].get("confidence", 0.0))
        keep_indices.add(best)
        removed += len(indices) - 1

    kept = [block for idx, block in enumerate(blocks) if idx in keep_indices]
    return kept, removed


# ---------------------------------------------------------------------------
# 4. Confidence-based review routing
# ---------------------------------------------------------------------------

_ACCEPT_THRESHOLD = 0.90
_FLAG_THRESHOLD = 0.75


def review_status_for_confidence(confidence: float) -> str:
    """>=0.90 accepted | 0.75-0.90 flagged | <0.75 needs_review."""
    if confidence >= _ACCEPT_THRESHOLD:
        return "accepted"
    if confidence >= _FLAG_THRESHOLD:
        return "flagged"
    return "needs_review"


def apply_review_routing(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sets `review_status` on every block from its own confidence, and
    derives `needs_review` from it. Figures keep whatever their own
    confidence+caption rule (tasks._build_figure_blocks) already decided
    on top of that — this only ever ADDS a needs_review=True case for a
    figure (a high-confidence, captioned figure that this tier would call
    "flagged" is not forced into needs_review), never removes one.
    """
    for block in blocks:
        status = review_status_for_confidence(block.get("confidence", 0.0))
        block["review_status"] = status
        if block.get("type") == "figure":
            block["needs_review"] = bool(block.get("needs_review", True)) or status == "needs_review"
        else:
            block["needs_review"] = status == "needs_review"
    return blocks


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def postprocess_page_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Runs the full post-processing stage on one page's raw combined block
    list, before normalize.py's reading-order sort and schema assembly.

    Never raises: a failure here falls back to the original, unprocessed
    block list rather than losing a page's OCR output over a cleanup bug.
    """
    if not blocks:
        return blocks
    try:
        for block in blocks:
            if block.get("type") not in _STRUCTURE_TYPES:
                block["text"] = clean_text(block.get("text", ""))

        blocks, suppressed = suppress_blocks_inside_structures(blocks)
        blocks, merged = merge_duplicate_text_blocks(blocks)
        blocks = apply_review_routing(blocks)

        if suppressed or merged:
            logger.info(
                "postprocess_page_blocks: suppressed %d block(s) duplicated inside a "
                "table/figure region, merged %d overlapping duplicate block(s)",
                suppressed, merged,
            )
        return blocks
    except Exception:
        logger.exception("postprocess_page_blocks failed — returning blocks unprocessed")
        return blocks
