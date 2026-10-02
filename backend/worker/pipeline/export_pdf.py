# Purpose: Generates derived PDF artifacts (Phase 2 items 1/7/8) from the
# Common Output Schema — a searchable PDF (visible page image + invisible
# selectable text layer) today; structured/highlighted PDF variants reuse the
# same font-loading and page-image helpers.

import logging
import os
from typing import Any, Dict

import pymupdf

logger = logging.getLogger(__name__)

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# Script family -> bundled Unicode font file. Matches router._SCRIPT_RANGES /
# _PRIMARY_MODEL_SCRIPTS: the fine-tuned recognition model (and therefore the
# block["language"] values this pipeline ever produces) only ever claims
# en/hi/mr/gu, so three font files cover every real block.
_LANGUAGE_FONTS: Dict[str, str] = {
    "hi": os.path.join(_FONTS_DIR, "NotoSansDevanagari-Regular.ttf"),
    "mr": os.path.join(_FONTS_DIR, "NotoSansDevanagari-Regular.ttf"),
    "gu": os.path.join(_FONTS_DIR, "NotoSansGujarati-Regular.ttf"),
}
_DEFAULT_FONT = os.path.join(_FONTS_DIR, "NotoSans-Regular.ttf")

# PyMuPDF's insert_font() rejects fontnames containing spaces (raises
# "bad fontname chars") — pymupdf.Font(...).name returns a human-readable
# name with spaces ("Noto Sans Devanagari Regular"), so a fixed, sanitized
# alias per font file is used as the PDF-internal /BaseFont name instead.
_FONT_ALIASES: Dict[str, str] = {
    os.path.join(_FONTS_DIR, "NotoSansDevanagari-Regular.ttf"): "notosans-deva",
    os.path.join(_FONTS_DIR, "NotoSansGujarati-Regular.ttf"): "notosans-gujr",
    os.path.join(_FONTS_DIR, "NotoSans-Regular.ttf"): "notosans-latn",
}

# PyMuPDF text render mode 3 = invisible (used for OCR text layers — visible
# page image on top, selectable/searchable text underneath, nothing drawn
# twice).
_RENDER_MODE_INVISIBLE = 3
_RENDER_MODE_FILL = 0

# Legend color per language (RGB, 0-1) for the highlighted PDF (item 8) — a
# semi-transparent rectangle in this color is drawn behind each block's text,
# matched to a legend printed at the top of each page.
_LANGUAGE_COLORS: Dict[str, tuple] = {
    "en": (0.25, 0.55, 0.95),   # blue
    "hi": (0.95, 0.55, 0.15),   # orange
    "mr": (0.25, 0.75, 0.45),   # green
    "gu": (0.85, 0.25, 0.55),   # magenta
}
_DEFAULT_LANGUAGE_COLOR = (0.6, 0.6, 0.6)  # grey — "und" or anything unexpected
_HIGHLIGHT_OPACITY = 0.30


def _font_for_language(language: str) -> str:
    return _LANGUAGE_FONTS.get(language, _DEFAULT_FONT)


def _fit_fontsize(text: str, bbox_w: float, bbox_h: float) -> float:
    """
    Picks an invisible-text fontsize that makes the text span roughly as
    wide as its own bbox, capped by the bbox height — doesn't need to be
    visually exact (the text is invisible), just close enough that
    page.search_for() hit-boxes land inside the block's own bbox.
    """
    if not text:
        return 10.0
    # Rough average glyph-width heuristic (~0.5x fontsize per character for
    # the mixed Latin/Devanagari/Gujarati scripts this pipeline handles).
    width_based = (bbox_w / max(len(text), 1)) / 0.5
    height_based = bbox_h * 0.9
    return max(4.0, min(width_based, height_based, 72.0))


def create_searchable_pdf(
    normalized_output: Dict[str, Any],
    page_images: Dict[int, bytes],
) -> bytes:
    """
    Builds a searchable PDF: one page per entry in normalized_output["pages"],
    each page's rasterized image as the visible background, plus an invisible
    text layer positioned at every text block's bbox so the page is
    selectable/searchable while looking identical to the plain page image.

    Args:
        normalized_output: Common Output Schema dict (normalize_to_common_schema output).
        page_images: {page_number: PNG bytes} — the same rasterized page images
            already uploaded to results/{job_id}/page_{n}.png, keyed by page_number.

    Returns:
        PDF file bytes.
    """
    doc = pymupdf.open()

    for page_data in normalized_output.get("pages", []):
        page_number = page_data["page_number"]
        image_bytes = page_images.get(page_number)
        if image_bytes is None:
            logger.warning(
                "create_searchable_pdf: no page image for page %d, skipping", page_number
            )
            continue

        img_doc = pymupdf.open(stream=image_bytes, filetype="png")
        pix = img_doc[0].get_pixmap()
        width, height = pix.width, pix.height
        img_doc.close()

        page = doc.new_page(width=width, height=height)
        page.insert_image(pymupdf.Rect(0, 0, width, height), stream=image_bytes)

        for block in page_data.get("blocks", []):
            text = block.get("text", "")
            if not text:
                continue

            bbox = block.get("bbox", [0.0, 0.0, 0.0, 0.0])
            x0, y0, x1, y1 = bbox
            bbox_w, bbox_h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)

            language = block.get("language", "en")
            font_path = _font_for_language(language)
            font_alias = _FONT_ALIASES[font_path]

            fontsize = _fit_fontsize(text, bbox_w, bbox_h)
            try:
                page.insert_text(
                    (x0, y0 + bbox_h * 0.85),
                    text,
                    fontsize=fontsize,
                    fontfile=font_path,
                    fontname=font_alias,
                    render_mode=_RENDER_MODE_INVISIBLE,
                )
            except Exception:
                logger.warning(
                    "create_searchable_pdf: failed to place invisible text for block_id=%s, skipping",
                    block.get("block_id"),
                    exc_info=True,
                )

    pdf_bytes = doc.tobytes()
    doc.close()
    logger.info(
        "create_searchable_pdf: built %d-page PDF (%d bytes)",
        len(normalized_output.get("pages", [])),
        len(pdf_bytes),
    )
    return pdf_bytes


def create_highlighted_pdf(
    normalized_output: Dict[str, Any],
    page_images: Dict[int, bytes],
) -> bytes:
    """
    Builds a language-highlighted PDF (Phase 2 item 8): same visible page
    image + invisible searchable text layer as create_searchable_pdf(), plus
    a semi-transparent color rectangle behind every text block keyed to its
    detected language, with a legend printed at the top of each page so a
    reviewer can see at a glance which regions were read as which language.
    """
    doc = pymupdf.open()
    languages_seen = set()

    for page_data in normalized_output.get("pages", []):
        page_number = page_data["page_number"]
        image_bytes = page_images.get(page_number)
        if image_bytes is None:
            logger.warning("create_highlighted_pdf: no page image for page %d, skipping", page_number)
            continue

        img_doc = pymupdf.open(stream=image_bytes, filetype="png")
        pix = img_doc[0].get_pixmap()
        width, height = pix.width, pix.height
        img_doc.close()

        page = doc.new_page(width=width, height=height)
        page.insert_image(pymupdf.Rect(0, 0, width, height), stream=image_bytes)

        for block in page_data.get("blocks", []):
            text = block.get("text", "")
            if not text:
                continue

            bbox = block.get("bbox", [0.0, 0.0, 0.0, 0.0])
            x0, y0, x1, y1 = bbox
            bbox_w, bbox_h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)

            language = block.get("language", "en")
            languages_seen.add(language)
            color = _LANGUAGE_COLORS.get(language, _DEFAULT_LANGUAGE_COLOR)

            page.draw_rect(
                pymupdf.Rect(x0, y0, x1, y1),
                color=None,
                fill=color,
                fill_opacity=_HIGHLIGHT_OPACITY,
            )

            font_path = _font_for_language(language)
            font_alias = _FONT_ALIASES[font_path]
            fontsize = _fit_fontsize(text, bbox_w, bbox_h)
            try:
                page.insert_text(
                    (x0, y0 + bbox_h * 0.85),
                    text,
                    fontsize=fontsize,
                    fontfile=font_path,
                    fontname=font_alias,
                    render_mode=_RENDER_MODE_INVISIBLE,
                )
            except Exception:
                logger.warning(
                    "create_highlighted_pdf: failed to place invisible text for block_id=%s, skipping",
                    block.get("block_id"), exc_info=True,
                )

        # Legend — small swatches + language codes, top-left corner.
        legend_x, legend_y = 10.0, 10.0
        for language in sorted(languages_seen):
            color = _LANGUAGE_COLORS.get(language, _DEFAULT_LANGUAGE_COLOR)
            page.draw_rect(
                pymupdf.Rect(legend_x, legend_y, legend_x + 12, legend_y + 12),
                color=None, fill=color, fill_opacity=0.9,
            )
            page.insert_text(
                (legend_x + 16, legend_y + 10), language,
                fontsize=10, fontname="helv", color=(0, 0, 0),
            )
            legend_x += 50

    pdf_bytes = doc.tobytes()
    doc.close()
    logger.info(
        "create_highlighted_pdf: built %d-page PDF (%d bytes), languages=%s",
        len(normalized_output.get("pages", [])), len(pdf_bytes), sorted(languages_seen),
    )
    return pdf_bytes


def _table_to_grid(table: Dict[str, Any]) -> list:
    """Shared grid-builder (rows x cols of cell text) from the
    table.{rows,cols,cells} schema — same expansion logic as
    normalize._table_to_markdown()/_table_to_txt(), reimplemented here so
    export_pdf.py has no dependency on normalize.py internals."""
    rows = table.get("rows", 0)
    cols = table.get("cols", 0)
    if rows == 0 or cols == 0:
        return []
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in table.get("cells", []):
        r0, c0 = cell.get("row", 0), cell.get("col", 0)
        row_span = max(1, cell.get("row_span", 1))
        col_span = max(1, cell.get("col_span", 1))
        text = cell.get("text") or ""
        for r in range(r0, min(r0 + row_span, rows)):
            for c in range(c0, min(c0 + col_span, cols)):
                grid[r][c] = text
    return grid


def create_structured_pdf(
    normalized_output: Dict[str, Any],
    page_images: Dict[int, bytes],
    fetch_crop: Any = None,
) -> bytes:
    """
    Builds a structured reconstruction PDF (Phase 2 item 7): a blank page per
    document page, sized to match the original rasterized page, with text
    blocks redrawn as real visible text at their bbox position (in reading
    order), tables redrawn as an actual grid with cell text, and figures
    re-embedded from their MinIO crop — a clean "reconstructed document"
    rather than an annotated scan (contrast with create_searchable_pdf/
    create_highlighted_pdf, which both overlay the original page image).

    Args:
        normalized_output: Common Output Schema dict.
        page_images: {page_number: PNG bytes} — used only for page dimensions.
        fetch_crop: optional callable(image_url: str) -> bytes, used to fetch
            a figure block's cropped image for re-embedding. Figures are
            skipped (logged) if not provided or if the fetch fails.
    """
    doc = pymupdf.open()

    for page_data in normalized_output.get("pages", []):
        page_number = page_data["page_number"]
        image_bytes = page_images.get(page_number)
        if image_bytes:
            img_doc = pymupdf.open(stream=image_bytes, filetype="png")
            pix = img_doc[0].get_pixmap()
            width, height = pix.width, pix.height
            img_doc.close()
        else:
            width, height = 1654, 2339  # fallback: ~A4 at 200 DPI

        page = doc.new_page(width=width, height=height)

        for block in page_data.get("blocks", []):
            block_type = block.get("type")
            bbox = block.get("bbox", [0.0, 0.0, 0.0, 0.0])
            x0, y0, x1, y1 = bbox
            bbox_w, bbox_h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)

            if block_type == "table":
                grid = _table_to_grid(block.get("table", {}))
                if not grid:
                    continue
                n_rows, n_cols = len(grid), len(grid[0])
                row_h, col_w = bbox_h / n_rows, bbox_w / n_cols
                for r in range(n_rows + 1):
                    y = y0 + r * row_h
                    page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=0.5)
                for c in range(n_cols + 1):
                    x = x0 + c * col_w
                    page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=0.5)
                for r in range(n_rows):
                    for c in range(n_cols):
                        cell_text = grid[r][c]
                        if not cell_text:
                            continue
                        cx, cy = x0 + c * col_w + 2, y0 + r * row_h + row_h * 0.7
                        try:
                            page.insert_text(
                                (cx, cy), cell_text,
                                fontsize=min(9.0, row_h * 0.6),
                                fontname="helv", render_mode=_RENDER_MODE_FILL,
                                color=(0, 0, 0),
                            )
                        except Exception:
                            logger.warning("create_structured_pdf: failed to draw table cell text", exc_info=True)

            elif block_type == "figure":
                image_url = block.get("image_url")
                if not image_url or fetch_crop is None:
                    logger.info(
                        "create_structured_pdf: skipping figure block_id=%s (no image_url/fetch_crop)",
                        block.get("block_id"),
                    )
                    continue
                try:
                    crop_bytes = fetch_crop(image_url)
                    page.insert_image(pymupdf.Rect(x0, y0, x1, y1), stream=crop_bytes)
                except Exception:
                    logger.warning(
                        "create_structured_pdf: failed to embed figure crop for block_id=%s",
                        block.get("block_id"), exc_info=True,
                    )

            else:
                text = block.get("text", "")
                if not text:
                    continue
                language = block.get("language", "en")
                font_path = _font_for_language(language)
                font_alias = _FONT_ALIASES[font_path]
                try:
                    page.insert_textbox(
                        pymupdf.Rect(x0, y0, x1, y1 + bbox_h * 0.5),  # headroom for wrapped lines
                        text,
                        fontsize=min(11.0, max(6.0, bbox_h * 0.7)),
                        fontfile=font_path,
                        fontname=font_alias,
                        render_mode=_RENDER_MODE_FILL,
                        color=(0, 0, 0),
                    )
                except Exception:
                    logger.warning(
                        "create_structured_pdf: failed to draw text for block_id=%s",
                        block.get("block_id"), exc_info=True,
                    )

    pdf_bytes = doc.tobytes()
    doc.close()
    logger.info(
        "create_structured_pdf: built %d-page PDF (%d bytes)",
        len(normalized_output.get("pages", [])), len(pdf_bytes),
    )
    return pdf_bytes
