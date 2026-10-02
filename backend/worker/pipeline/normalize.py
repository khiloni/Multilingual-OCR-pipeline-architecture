# Purpose: Normalize raw dictionary outputs from routing engines into unified JSON schema and Markdown text.
# Future TODOs: Add Markdown table generator formatting, list parsing, and reading order sort algorithms.

import logging
from typing import List, Dict, Any, Tuple
from datetime import datetime
from uuid import UUID

from app.core.config import settings
from worker.pipeline.correction import correct_page_blocks
from worker.pipeline.spellcheck import CURRENT_LANGS, correct_block_text

logger = logging.getLogger(__name__)

# Minimum horizontal-overlap fraction (relative to the narrower of the two
# spans being compared) for a block to join an existing column cluster.
# Chosen conservatively: two blocks that only share a sliver of x-range
# (e.g. a full-width heading vs. a narrow column) should NOT be merged into
# the same column, but two blocks from the same text column — which rarely
# align pixel-perfectly due to justification/indentation — should.
_COLUMN_OVERLAP_THRESHOLD: float = 0.5


def sort_reading_order(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sorts blocks into natural reading order for both single- and
    multi-column layouts:

      1. Cluster blocks into columns by horizontal (x-axis) bbox overlap.
      2. Order columns left-to-right by their leftmost edge.
      3. Within each column, sort blocks top-to-bottom by y0.

    PaddleOCR/PaddleX's PP-StructureV3 pipeline can produce a native
    reading-order via its layout-parsing (XY-cut) stage, which would be
    preferable to a hand-rolled heuristic — but it re-runs text detection
    *and* recognition on top of layout detection, which is prohibitively
    slow on this CPU-only deployment for a page that already went through
    PaddleOCR/Surya once. This heuristic operates purely on already-computed
    bboxes (no extra model inference), so it costs nothing extra per page.

    A plain top-to-bottom sort (the previous implementation) silently
    interleaves columns on any multi-column document — e.g. a 2-column
    page reads left-col-line-1, right-col-line-1, left-col-line-2, ...
    instead of finishing the left column first. This function fixes that
    while staying a no-op for single-column pages (every block ends up in
    one cluster, so the result is the same top-to-bottom order as before).
    """
    if not blocks:
        return blocks

    # Process left-to-right so earlier (further-left) blocks seed columns
    # before later ones need to decide which column they belong to.
    blocks_by_x = sorted(blocks, key=lambda b: b["bbox"][0])

    columns: List[List[Dict[str, Any]]] = []
    column_ranges: List[Tuple[float, float]] = []  # (min_x0, max_x1) per column

    for block in blocks_by_x:
        x0, _, x1, _ = block["bbox"]
        placed = False
        for idx, (col_x0, col_x1) in enumerate(column_ranges):
            overlap = min(x1, col_x1) - max(x0, col_x0)
            narrower_width = min(x1 - x0, col_x1 - col_x0)
            if narrower_width > 0 and (overlap / narrower_width) >= _COLUMN_OVERLAP_THRESHOLD:
                columns[idx].append(block)
                column_ranges[idx] = (min(col_x0, x0), max(col_x1, x1))
                placed = True
                break
        if not placed:
            columns.append([block])
            column_ranges.append((x0, x1))

    column_order = sorted(range(len(columns)), key=lambda i: column_ranges[i][0])

    result: List[Dict[str, Any]] = []
    for idx in column_order:
        result.extend(sorted(columns[idx], key=lambda b: b["bbox"][1]))

    logger.debug(
        "sort_reading_order: %d blocks clustered into %d column(s)",
        len(blocks), len(columns),
    )
    return result

def normalize_to_common_schema(
    document_id: UUID,
    filename: str,
    raw_pages: Dict[int, List[Dict[str, Any]]],
    page_ocr_attempts: Dict[int, str] = None,
) -> Dict[str, Any]:
    """
    Accepts raw extraction outputs per page, sorts block layout, and serializes into standard format.
    Matches Section 10 of ARCHITECTURE.md.

    page_ocr_attempts: optional {page_number: "original"|"enhanced"|
    "threshold"|"surya"|"error"} from router.EngineRouter.process_page()
    (Phase 2 item 2) — which image variant/engine the accepted result
    came from. Defaults to "original" for any page not present.
    """
    page_ocr_attempts = page_ocr_attempts or {}
    logger.info(f"Normalizing OCR results for document: {document_id}")
    pages_list = []
    all_confidences = []
    low_confidence_pages = []

    for page_num, raw_blocks in raw_pages.items():
        sorted_blocks = sort_reading_order(raw_blocks)

        # block_id is assigned up front (not inline in the loop below)
        # because the correction stage needs stable ids to key its
        # response by, before any per-type branching happens.
        for idx, block in enumerate(sorted_blocks):
            block["block_id"] = f"p{page_num}_b{idx + 1}"

        # API-based correction (Phase 2 item 0b) — ONE batched call for
        # every eligible block on this page. correct_page_blocks() already
        # skips table/figure blocks and high-confidence text internally,
        # and never raises (see worker/pipeline/correction.py); result
        # list is parallel to sorted_blocks, same order, same length.
        corrections = correct_page_blocks(sorted_blocks)

        page_blocks = []
        page_languages = set()

        for block, correction in zip(sorted_blocks, corrections):
            block_id = block["block_id"]
            block_type = block.get("type", "paragraph")
            bbox = block.get("bbox", [0.0, 0.0, 0.0, 0.0])
            confidence = block.get("confidence", 0.0)
            all_confidences.append(confidence)

            # review_status/needs_review are set upstream by
            # postprocess.apply_review_routing() for every block type —
            # see worker/pipeline/postprocess.py. Defaults here only cover
            # blocks that somehow bypassed that stage.
            review_status = block.get("review_status", "accepted")

            if block_type == "table":
                # ARCHITECTURE.md §10.2 — structured cell data, no top-level
                # "text"/"language"/"engine_used"/spell-check (cell text
                # isn't spell-corrected; it's numeric/short-label heavy and
                # the table pipeline already ran its own OCR pass on it).
                page_blocks.append({
                    "block_id": block_id,
                    "type": "table",
                    "bbox": bbox,
                    "confidence": confidence,
                    "table": block.get("table", {"rows": 0, "cols": 0, "cells": []}),
                    "review_status": review_status,
                    "needs_review": block.get("needs_review", review_status == "needs_review"),
                })
                continue

            if block_type == "figure":
                # ARCHITECTURE.md §10.3. "_crop_bytes" passes through
                # untouched — process_ocr() consumes it in a post-normalize
                # pass (needs the real block_id, assigned right above, to
                # name the MinIO object) and strips it before serialization.
                figure_block = {
                    "block_id": block_id,
                    "type": "figure",
                    "subtype": block.get("subtype", "image"),
                    "bbox": bbox,
                    "confidence": confidence,
                    "image_url": block.get("image_url"),
                    "caption": block.get("caption"),
                    "alt_text": block.get("alt_text"),
                    "review_status": review_status,
                    "needs_review": block.get("needs_review", True),
                }
                if "_crop_bytes" in block:
                    figure_block["_crop_bytes"] = block["_crop_bytes"]
                page_blocks.append(figure_block)
                continue

            # Text-bearing block types (heading/paragraph/list/caption).
            lang = block.get("language", "en")
            engine_used = block.get("engine_used", "paddleocr")

            # legacy_symspell: trivial opt-back-in to the old offline path,
            # kept only for comparison — not the default. See CORRECTION_
            # PROVIDER in config.py / ARCHITECTURE.md §9.5 for why the API
            # path replaced it (gu/mr dictionary + lookup_compound() issues).
            if settings.CORRECTION_PROVIDER == "legacy_symspell":
                text = block.get("text", "")
                if text and lang in CURRENT_LANGS:
                    token_confidences = [confidence] * len(text.split())
                    spell_result = correct_block_text(text, lang, token_confidences=token_confidences)
                    text = spell_result.text
                original_text = block.get("text", "")
                corrected_text = text
                correction_applied = corrected_text != original_text
            else:
                original_text = correction.original_text
                corrected_text = correction.corrected_text
                correction_applied = correction.applied

            if lang:
                page_languages.add(lang)

            # Drop review gating for text once a correction was actually
            # applied — the point of item 0b is output that needs no human
            # recheck. If correction wasn't applied (disabled/failed/
            # skipped), the confidence-tier signal from postprocess.py
            # still stands.
            needs_review = block.get("needs_review", review_status == "needs_review")
            if correction_applied:
                needs_review = False

            page_blocks.append({
                "block_id": block_id,
                "type": block_type,
                "text": corrected_text,
                "original_text": original_text,
                "corrected_text": corrected_text,
                "correction_applied": correction_applied,
                "bbox": bbox,
                "confidence": confidence,
                "language": lang,
                "engine_used": engine_used,
                "review_status": review_status,
                "needs_review": needs_review,
            })

        # Check if page has low confidence blocks
        page_avg_conf = sum([b["confidence"] for b in page_blocks]) / len(page_blocks) if page_blocks else 0.0
        if page_avg_conf < 0.8:
            low_confidence_pages.append(page_num)

        pages_list.append({
            "page_number": page_num,
            "language_detected": list(page_languages),
            "ocr_attempt": page_ocr_attempts.get(page_num, "original"),
            "blocks": page_blocks
        })

    avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0

    return {
        "document_id": str(document_id),
        "filename": filename,
        "page_count": len(raw_pages),
        "pages": pages_list,
        "metadata": {
            "processed_at": datetime.utcnow().isoformat() + "Z",
            "avg_confidence": round(avg_confidence, 4),
            "low_confidence_pages": low_confidence_pages
        }
    }

def _table_to_markdown(table: Dict[str, Any]) -> str:
    """
    Best-effort GFM table export from the structured table.{rows,cols,cells}
    shape. GFM can't express row_span/col_span (ARCHITECTURE.md §10.2), so a
    merged cell's text is repeated at every position it spans — the JSON
    `table` field stays the authoritative source of true span data.
    """
    rows = table.get("rows", 0)
    cols = table.get("cols", 0)
    if rows == 0 or cols == 0:
        return ""

    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in table.get("cells", []):
        r0, c0 = cell.get("row", 0), cell.get("col", 0)
        row_span = max(1, cell.get("row_span", 1))
        col_span = max(1, cell.get("col_span", 1))
        text = (cell.get("text") or "").replace("|", "\\|").replace("\n", " ")
        for r in range(r0, min(r0 + row_span, rows)):
            for c in range(c0, min(c0 + col_span, cols)):
                grid[r][c] = text

    lines = ["| " + " | ".join(grid[0]) + " |"]
    lines.append("| " + " | ".join(["---"] * cols) + " |")
    for row in grid[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def convert_to_markdown(schema_data: Dict[str, Any]) -> str:
    """
    Transforms the unified JSON schema blocks into human-readable Markdown
    format (headings, lists, GFM tables, figure images — ARCHITECTURE.md
    §10/§10.2/§10.3). JSON stays the source of truth; this is a derived
    export, always regenerated from the same schema_data, never hand-edited.
    """
    logger.info("Converting normalized JSON schema elements to Markdown text")
    markdown_lines = []

    for page in schema_data.get("pages", []):
        markdown_lines.append(f"<!-- Page {page['page_number']} -->\n")
        for block in page.get("blocks", []):
            b_type = block.get("type")

            if b_type == "heading":
                markdown_lines.append(f"# {block.get('text', '')}\n")
            elif b_type == "list":
                markdown_lines.append(f"- {block.get('text', '')}\n")
            elif b_type == "table":
                md_table = _table_to_markdown(block.get("table", {}))
                markdown_lines.append(f"\n{md_table}\n")
            elif b_type == "figure":
                caption = block.get("caption") or "figure"
                image_url = block.get("image_url") or ""
                markdown_lines.append(f"\n![{caption}]({image_url})")
                if block.get("caption"):
                    markdown_lines.append(f"*{block['caption']}*\n")
                else:
                    markdown_lines.append("")
            else:
                markdown_lines.append(f"{block.get('text', '')}\n")
        markdown_lines.append("\n")

    return "\n".join(markdown_lines)


def _table_to_txt(table: Dict[str, Any]) -> str:
    """Tab-separated grid export from the structured table.{rows,cols,cells} shape."""
    rows = table.get("rows", 0)
    cols = table.get("cols", 0)
    if rows == 0 or cols == 0:
        return ""

    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in table.get("cells", []):
        r0, c0 = cell.get("row", 0), cell.get("col", 0)
        row_span = max(1, cell.get("row_span", 1))
        col_span = max(1, cell.get("col_span", 1))
        text = (cell.get("text") or "").replace("\t", " ").replace("\n", " ")
        for r in range(r0, min(r0 + row_span, rows)):
            for c in range(c0, min(c0 + col_span, cols)):
                grid[r][c] = text

    return "\n".join("\t".join(row) for row in grid)


_TXT_PAGE_MARKER = "=" * 20


def convert_to_txt(schema_data: Dict[str, Any]) -> str:
    """
    Transforms the unified JSON schema blocks into plain text, page by page,
    in the same reading order already established by
    normalize_to_common_schema(). Tables are flattened row-by-row
    (tab-separated); figures contribute their caption only (no markup, no
    image reference — plain text has nowhere to put one).
    """
    logger.info("Converting normalized JSON schema elements to plain text")
    lines = []

    for page in schema_data.get("pages", []):
        lines.append(f"{_TXT_PAGE_MARKER} Page {page['page_number']} {_TXT_PAGE_MARKER}")
        lines.append("")
        for block in page.get("blocks", []):
            b_type = block.get("type")

            if b_type == "table":
                lines.append(_table_to_txt(block.get("table", {})))
                lines.append("")
            elif b_type == "figure":
                if block.get("caption"):
                    lines.append(f"[Figure: {block['caption']}]")
                    lines.append("")
            else:
                text = block.get("text", "")
                if text:
                    lines.append(text)
        lines.append("")

    return "\n".join(lines)
