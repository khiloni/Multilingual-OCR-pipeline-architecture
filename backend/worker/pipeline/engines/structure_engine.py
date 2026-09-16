# Purpose: Layout-aware structure detection — tables (Phase 1 item 4) and
# figures/charts (Phase 1 item 5) — using PaddleX's TableRecognitionPipelineV2.
#
# ONE call to TableRecognitionPipelineV2.predict() per page returns BOTH:
#   - table_res_list: table structure (cell boxes, per-cell OCR, HTML) for
#     any detected table region.
#   - layout_det_res: the full-page layout detection (PP-DocLayout-L) used
#     internally to find tables in the first place — which also happens to
#     label figure/chart/title regions, so a second, separate LayoutDetection
#     pass isn't needed for item 5.
#
# This engine does NOT do its own text-line OCR for the rest of the page —
# that stays owned by router.py's PaddleOCR/Surya pass (ARCHITECTURE.md §9).
# It reuses the fine-tuned recognition model (PADDLEOCR_REC_MODEL_DIR) only
# for the text found inside detected table cells.
#
# Runs unconditionally per page (not confidence-gated like the Surya
# fallback) — tables/figures need to be caught regardless of how confident
# the plain text recognition was.

import logging
import os
import re
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_DET_MODEL_NAME = "PP-OCRv6_medium_det"
_REC_MODEL_NAME = "PP-OCRv6_medium_rec"
_CUSTOM_REC_MODEL_DIR = os.environ.get("PADDLEOCR_REC_MODEL_DIR")

# Layout labels (from PP-DocLayout-L, confirmed empirically — see
# ARCHITECTURE.md changelog) that map to a Common Output Schema "figure"
# block. "chart" gets subtype="chart"; everything else here gets "image".
_CHART_LABELS = {"chart"}
_FIGURE_LABELS = {"image", "figure", "picture"} | _CHART_LABELS

# Labels whose OCR'd text is a caption candidate for a nearby figure/table.
_TITLE_LABELS = {"table_title", "chart_title", "figure_title"}

# ARCHITECTURE.md §10.3: caption text must match this pattern near the bbox.
_CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table)\s*\d+", re.IGNORECASE)

# needs_review=False only when confidence clears this bar AND a caption matched.
_FIGURE_REVIEW_CONFIDENCE_THRESHOLD = 0.85

# How far (in px, at the pipeline's working resolution) a caption candidate's
# bbox may sit from a figure/table's bbox and still count as "nearby".
_CAPTION_PROXIMITY_PX = 80.0


class StructureEngine:
    """
    Lazily-initialised (same pattern as PaddleOCREngine/SuryaOCREngine)
    wrapper around PaddleX's TableRecognitionPipelineV2.
    """

    def __init__(self) -> None:
        self._pipeline = None
        logger.info("StructureEngine created (table/layout models load lazily on first call)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_page(self, image: np.ndarray) -> Dict[str, List[Dict[str, Any]]]:
        """
        Runs table + layout detection on a preprocessed page image.

        Returns:
            {
                "tables": [table block dicts, minus block_id — bbox/type/
                            confidence/table already set],
                "figure_regions": [{"bbox", "subtype", "confidence"}, ...]
                            — raw layout detections, NOT yet cropped/
                            captioned/uploaded (caller owns that, since it
                            needs job_id/MinIO access and the already-
                            extracted text blocks for caption matching).
            }
            Both lists are empty (not an exception) if detection fails —
            structure detection is additive; it must never break the page.
        """
        pipeline = self._get_pipeline()
        try:
            results = list(pipeline.predict(image))
        except Exception as exc:
            logger.error("TableRecognitionPipelineV2.predict() failed: %s", exc, exc_info=True)
            return {"tables": [], "figure_regions": []}

        if not results:
            return {"tables": [], "figure_regions": []}

        result = results[0]
        tables = self._parse_tables(result)
        figure_regions = self._parse_figure_regions(result)

        logger.info(
            "StructureEngine: %d table(s), %d figure/chart region(s) detected",
            len(tables), len(figure_regions),
        )
        return {"tables": tables, "figure_regions": figure_regions}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_pipeline(self):
        if self._pipeline is None:
            from paddleocr import TableRecognitionPipelineV2

            kwargs: Dict[str, Any] = dict(
                text_detection_model_name=_DET_MODEL_NAME,
                text_recognition_model_name=_REC_MODEL_NAME,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                # CRITICAL — see paddle_engine.py for the full explanation:
                # without this, PaddlePaddle 3.3.x's oneDNN runtime raises
                # "ConvertPirAttribute2RuntimeAttribute not supported" on
                # CPU inference. Confirmed reproduced and fixed here too.
                enable_mkldnn=False,
            )
            if _CUSTOM_REC_MODEL_DIR:
                kwargs["text_recognition_model_dir"] = _CUSTOM_REC_MODEL_DIR
                logger.info(
                    "StructureEngine loading fine-tuned rec model from %s",
                    _CUSTOM_REC_MODEL_DIR,
                )
            logger.info("Initialising TableRecognitionPipelineV2 (weights download on first run if not cached)")
            self._pipeline = TableRecognitionPipelineV2(**kwargs)
            logger.info("TableRecognitionPipelineV2 initialised successfully")
        return self._pipeline

    def _parse_tables(self, result: Any) -> List[Dict[str, Any]]:
        table_res_list = result.get("table_res_list") if hasattr(result, "get") else None
        if not table_res_list:
            return []

        tables: List[Dict[str, Any]] = []
        for table_res in table_res_list:
            try:
                block = self._parse_single_table(table_res)
            except Exception as exc:
                logger.warning("Skipping malformed table result: %s", exc, exc_info=True)
                continue
            if block is not None:
                tables.append(block)
        return tables

    @staticmethod
    def _bbox_from_cell_boxes(cell_box_list: List[Any]) -> List[float]:
        if not cell_box_list:
            return [0.0, 0.0, 0.0, 0.0]
        xs0, ys0, xs1, ys1 = [], [], [], []
        for box in cell_box_list:
            b = [float(v) for v in box]
            xs0.append(b[0]); ys0.append(b[1]); xs1.append(b[2]); ys1.append(b[3])
        return [min(xs0), min(ys0), max(xs1), max(ys1)]

    def _parse_single_table(self, table_res: Any) -> Optional[Dict[str, Any]]:
        cell_box_list = table_res.get("cell_box_list")
        cell_box_list = [] if cell_box_list is None else cell_box_list
        pred_html = table_res.get("pred_html") or ""
        table_ocr_pred = table_res.get("table_ocr_pred") or {}
        # rec_scores comes back as a numpy ndarray, not a list — `x or []`
        # raises "truth value of an array is ambiguous" for multi-element
        # arrays, so this must be an explicit None check + list() convert.
        rec_scores = table_ocr_pred.get("rec_scores")
        rec_scores = [] if rec_scores is None else list(rec_scores)

        structure = _parse_table_html(pred_html, rec_scores)
        if structure["rows"] == 0:
            return None

        confidences = [c["confidence"] for c in structure["cells"]]
        block_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

        return {
            "type": "table",
            "bbox": self._bbox_from_cell_boxes(cell_box_list),
            "confidence": block_confidence,
            "table": structure,
        }

    def _parse_figure_regions(self, result: Any) -> List[Dict[str, Any]]:
        layout = result.get("layout_det_res") if hasattr(result, "get") else None
        if not layout:
            return []
        boxes = layout.get("boxes") if hasattr(layout, "get") else None
        if not boxes:
            return []

        regions: List[Dict[str, Any]] = []
        for box in boxes:
            label = box.get("label")
            if label not in _FIGURE_LABELS:
                continue
            coord = box.get("coordinate")
            if coord is None or len(coord) != 4:
                continue
            regions.append({
                "bbox": [float(v) for v in coord],
                "subtype": "chart" if label in _CHART_LABELS else "image",
                "confidence": float(box.get("score", 0.0)),
            })
        return regions

    def extract_title_candidates(self, result_boxes: Any) -> List[Dict[str, Any]]:
        """Unused placeholder retained for symmetry — caption matching is
        done by the caller against already-extracted OCR text blocks
        (see tasks.py), not against this engine's own title-label boxes,
        since ARCHITECTURE.md §10.3 specifies regex matching on OCR'd text,
        not on the layout model's title/non-title classification alone."""
        return []


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions — no engine state)
# ---------------------------------------------------------------------------

def _parse_table_html(html: str, rec_scores: List[float]) -> Dict[str, Any]:
    """
    Parses PaddleX's predicted table HTML (e.g. "<table><tr><td>...") into
    the Common Output Schema's table.{rows,cols,cells} shape (ARCHITECTURE.md
    §10), including row_span/col_span from the HTML's own rowspan/colspan
    attributes.

    Per-cell confidence is taken from table_ocr_pred.rec_scores by position
    — the HTML cells and rec_scores are both produced from the same
    structure-recognition pass over the same detected cells in the same
    (row-major) order, so a positional zip is reliable; there is no shared
    per-cell key to join on instead.

    is_header heuristic: a cell is a header if the HTML itself marks it
    <th>, OR it's in row 0 (no <thead>/<th> was observed in practice from
    this pipeline version — every cell comes back as <td> — so row-0-is-
    header is the practical signal, consistent with the vast majority of
    real tables; documented here as a heuristic, not a certainty).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table_tag = soup.find("table")
    if table_tag is None:
        return {"rows": 0, "cols": 0, "cells": []}

    rows = table_tag.find_all("tr")
    grid_occupied: Dict[tuple, bool] = {}
    cells: List[Dict[str, Any]] = []
    cell_idx = 0
    max_col = 0

    for row_idx, tr in enumerate(rows):
        col_idx = 0
        for cell_tag in tr.find_all(["td", "th"]):
            while grid_occupied.get((row_idx, col_idx)):
                col_idx += 1
            try:
                row_span = int(cell_tag.get("rowspan", 1))
            except (TypeError, ValueError):
                row_span = 1
            try:
                col_span = int(cell_tag.get("colspan", 1))
            except (TypeError, ValueError):
                col_span = 1
            text = cell_tag.get_text(strip=True)
            is_header = cell_tag.name == "th" or row_idx == 0
            confidence = float(rec_scores[cell_idx]) if cell_idx < len(rec_scores) else 0.0

            cells.append({
                "row": row_idx,
                "col": col_idx,
                "row_span": row_span,
                "col_span": col_span,
                "text": text,
                "is_header": is_header,
                "confidence": round(confidence, 4),
            })

            for r in range(row_span):
                for c in range(col_span):
                    grid_occupied[(row_idx + r, col_idx + c)] = True

            col_idx += col_span
            max_col = max(max_col, col_idx)
            cell_idx += 1

    return {"rows": len(rows), "cols": max_col, "cells": cells}


def _bbox_center(bbox: List[float]) -> "tuple[float, float]":
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _bbox_vertical_gap(a: List[float], b: List[float]) -> float:
    """Vertical gap between two bboxes (0 if they overlap vertically)."""
    if a[3] < b[1]:
        return b[1] - a[3]
    if b[3] < a[1]:
        return a[1] - b[3]
    return 0.0


def find_caption(figure_bbox: List[float], text_blocks: List[Dict[str, Any]]) -> Optional[str]:
    """
    ARCHITECTURE.md §10.3: "text block near the bbox matching
    /^(Figure|Fig\\.|Table)\\s*\\d+/i". No match -> None, never "".

    Searches already-extracted OCR text blocks (the main Paddle/Surya pass,
    not this engine's own table-internal OCR) for the closest vertically-
    adjacent, horizontally-overlapping block whose text matches the pattern.
    """
    best_text: Optional[str] = None
    best_gap = _CAPTION_PROXIMITY_PX

    fx0, _, fx1, _ = figure_bbox
    for block in text_blocks:
        text = block.get("text", "")
        if not _CAPTION_RE.match(text.strip()):
            continue
        bbox = block.get("bbox")
        if not bbox:
            continue
        bx0, _, bx1, _ = bbox
        # require some horizontal overlap with the figure — a caption on
        # the far side of a 2-column page shouldn't attach to this figure.
        if bx1 < fx0 or bx0 > fx1:
            continue
        gap = _bbox_vertical_gap(figure_bbox, bbox)
        if gap <= best_gap:
            best_gap = gap
            best_text = text.strip()

    return best_text
