# Purpose: Layout-aware structure detection — tables and figures/charts.
#
# Per page:
#   1. Layout detection (one CNN pass, a few seconds on CPU) finds table /
#      chart / image regions across the whole page.
#   2. Figure and chart regions come straight from that layout result.
#   3. The table pipeline (structure recognition + cell OCR) runs ONLY on the
#      cropped table regions, and is only loaded the first time a table is
#      actually seen — a page with no table pays for layout detection and
#      nothing else.
#
# The earlier design ran the full table pipeline on the whole page every time,
# which internally re-ran full-page text detection and recognition — roughly
# 40-50s per page even when the page had no table at all.
#
# This engine does NOT do text-line OCR for the rest of the page — that stays
# owned by router.py's PaddleOCR/Surya pass. It reuses the fine-tuned
# recognition model (PADDLEOCR_REC_MODEL_DIR) only for text inside table cells.
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

_LAYOUT_MODEL_NAME = "PP-DocLayout-L"
_DET_MODEL_NAME = "PP-OCRv6_medium_det"
_REC_MODEL_NAME = "PP-OCRv6_medium_rec"
_CUSTOM_REC_MODEL_DIR = os.environ.get("PADDLEOCR_REC_MODEL_DIR")

# Layout labels that map to a Common Output Schema "figure" block.
# "chart" gets subtype="chart"; everything else here gets "image".
_CHART_LABELS = {"chart"}
_FIGURE_LABELS = {"image", "figure", "picture"} | _CHART_LABELS
_TABLE_LABELS = {"table"}

# ARCHITECTURE.md §9.3: caption text must match this pattern near the bbox.
_CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table)\s*\d+", re.IGNORECASE)

# How far (in px, at the pipeline's working resolution) a caption candidate's
# bbox may sit from a figure/table's bbox and still count as "nearby".
_CAPTION_PROXIMITY_PX = 80.0

# Extra pixels kept around a table region when cropping it for the table
# pipeline, so cell borders on the region edge aren't clipped.
_TABLE_CROP_PADDING_PX = 10


class StructureEngine:
    """
    Lazily-initialised (same pattern as PaddleOCREngine/SuryaOCREngine):
    the layout model loads on the first page, and the heavier table pipeline
    loads only when the first table is found.
    """

    def __init__(self) -> None:
        self._layout = None
        self._table_pipeline = None
        logger.info("StructureEngine created (models load lazily on first use)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_page(self, image: np.ndarray) -> Dict[str, List[Dict[str, Any]]]:
        """
        Runs layout + table detection on a preprocessed page image.

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
        try:
            layout_boxes = self._detect_layout(image)
        except Exception as exc:
            logger.error("Layout detection failed: %s", exc, exc_info=True)
            return {"tables": [], "figure_regions": []}

        figure_regions = self._figure_regions(layout_boxes)
        tables = self._detect_tables(image, layout_boxes)

        logger.info(
            "StructureEngine: %d table(s), %d figure/chart region(s) detected",
            len(tables), len(figure_regions),
        )
        return {"tables": tables, "figure_regions": figure_regions}

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _get_layout_model(self):
        if self._layout is None:
            from paddleocr import LayoutDetection

            logger.info("Initialising layout detection model (%s)", _LAYOUT_MODEL_NAME)
            # enable_mkldnn=False — see paddle_engine.py: the oneDNN runtime
            # crashes on CPU inference in this PaddlePaddle version.
            self._layout = LayoutDetection(model_name=_LAYOUT_MODEL_NAME, enable_mkldnn=False)
        return self._layout

    def _detect_layout(self, image: np.ndarray) -> List[Dict[str, Any]]:
        results = list(self._get_layout_model().predict(image))
        if not results:
            return []
        boxes = results[0].get("boxes") or []
        layout: List[Dict[str, Any]] = []
        for box in boxes:
            coord = box.get("coordinate")
            if coord is None or len(coord) != 4:
                continue
            layout.append({
                "label": box.get("label"),
                "score": float(box.get("score", 0.0)),
                "coordinate": [float(v) for v in coord],
            })
        return layout

    @staticmethod
    def _figure_regions(layout_boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "bbox": box["coordinate"],
                "subtype": "chart" if box["label"] in _CHART_LABELS else "image",
                "confidence": box["score"],
            }
            for box in layout_boxes
            if box["label"] in _FIGURE_LABELS
        ]

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def _get_table_pipeline(self):
        if self._table_pipeline is None:
            from paddleocr import TableRecognitionPipelineV2

            kwargs: Dict[str, Any] = dict(
                text_detection_model_name=_DET_MODEL_NAME,
                text_recognition_model_name=_REC_MODEL_NAME,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                # Layout was already done page-wide; this pipeline only ever
                # sees a crop that is the table, so skip its own layout pass.
                use_layout_detection=False,
                enable_mkldnn=False,
            )
            if _CUSTOM_REC_MODEL_DIR:
                kwargs["text_recognition_model_dir"] = _CUSTOM_REC_MODEL_DIR
                logger.info("Table pipeline using fine-tuned rec model from %s", _CUSTOM_REC_MODEL_DIR)
            logger.info("Initialising table recognition pipeline")
            self._table_pipeline = TableRecognitionPipelineV2(**kwargs)
        return self._table_pipeline

    def _detect_tables(
        self, image: np.ndarray, layout_boxes: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        h, w = image.shape[:2]
        tables: List[Dict[str, Any]] = []

        for box in layout_boxes:
            if box["label"] not in _TABLE_LABELS:
                continue
            x0, y0, x1, y1 = box["coordinate"]
            cx0 = max(0, int(x0) - _TABLE_CROP_PADDING_PX)
            cy0 = max(0, int(y0) - _TABLE_CROP_PADDING_PX)
            cx1 = min(w, int(x1) + _TABLE_CROP_PADDING_PX)
            cy1 = min(h, int(y1) + _TABLE_CROP_PADDING_PX)
            if cx1 <= cx0 or cy1 <= cy0:
                continue

            try:
                results = list(self._get_table_pipeline().predict(
                    image[cy0:cy1, cx0:cx1],
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_layout_detection=False,
                ))
            except Exception as exc:
                logger.error("Table recognition failed for region %s: %s", box["coordinate"], exc, exc_info=True)
                continue

            table_res_list = results[0].get("table_res_list") if results else None
            if not table_res_list:
                continue
            try:
                # bbox comes from layout detection (already in page
                # coordinates) rather than from the crop-relative cell boxes.
                block = self._parse_single_table(table_res_list[0], [float(x0), float(y0), float(x1), float(y1)])
            except Exception as exc:
                logger.warning("Skipping malformed table result: %s", exc, exc_info=True)
                continue
            if block is not None:
                tables.append(block)

        return tables

    @staticmethod
    def _parse_single_table(table_res: Any, bbox: List[float]) -> Optional[Dict[str, Any]]:
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
            "bbox": bbox,
            "confidence": block_confidence,
            "table": structure,
        }


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
