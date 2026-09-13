# Purpose: Normalize raw dictionary outputs from routing engines into unified JSON schema and Markdown text.
# Future TODOs: Add Markdown table generator formatting, list parsing, and reading order sort algorithms.

import logging
from typing import List, Dict, Any
from datetime import datetime
from uuid import UUID

from worker.pipeline.spellcheck import CURRENT_LANGS, correct_block_text

logger = logging.getLogger(__name__)

def sort_reading_order(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sorts blocks from top-to-bottom, left-to-right to ensure logical reading layout.
    """
    # TODO: Implement 2D sorting algorithm based on bbox layout.
    return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))

def normalize_to_common_schema(
    document_id: UUID,
    filename: str,
    raw_pages: Dict[int, List[Dict[str, Any]]]
) -> Dict[str, Any]:
    """
    Accepts raw extraction outputs per page, sorts block layout, and serializes into standard format.
    Matches Section 10 of ARCHITECTURE.md.
    """
    logger.info(f"Normalizing OCR results for document: {document_id}")
    pages_list = []
    all_confidences = []
    low_confidence_pages = []

    for page_num, raw_blocks in raw_pages.items():
        sorted_blocks = sort_reading_order(raw_blocks)
        page_blocks = []
        page_languages = set()

        for idx, block in enumerate(sorted_blocks):
            block_id = f"p{page_num}_b{idx + 1}"
            
            # Map parameters
            text = block.get("text", "")
            bbox = block.get("bbox", [0.0, 0.0, 0.0, 0.0])
            confidence = block.get("confidence", 0.0)
            lang = block.get("language", "en")
            block_type = block.get("type", "paragraph")
            engine_used = block.get("engine_used", "paddleocr")

            # Automated spell correction (only touches low-confidence tokens,
            # only for languages we have a fine-tuned model + dictionary for —
            # see worker/pipeline/spellcheck.py). No per-token OCR confidence
            # survives the engine layer, so the block's own confidence is used
            # uniformly across its tokens.
            spell_corrections = []
            if text and lang in CURRENT_LANGS:
                token_confidences = [confidence] * len(text.split())
                spell_result = correct_block_text(text, lang, token_confidences=token_confidences)
                text = spell_result.text
                spell_corrections = [
                    {
                        "original": c.original,
                        "corrected": c.corrected,
                        "edit_distance": c.edit_distance,
                    }
                    for c in spell_result.corrections
                ]

            all_confidences.append(confidence)
            if lang:
                page_languages.add(lang)

            page_blocks.append({
                "block_id": block_id,
                "type": block_type,
                "text": text,
                "bbox": bbox,
                "confidence": confidence,
                "language": lang,
                "engine_used": engine_used,
                "spell_corrections": spell_corrections,
            })

        # Check if page has low confidence blocks
        page_avg_conf = sum([b["confidence"] for b in page_blocks]) / len(page_blocks) if page_blocks else 0.0
        if page_avg_conf < 0.8:
            low_confidence_pages.append(page_num)

        pages_list.append({
            "page_number": page_num,
            "language_detected": list(page_languages),
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

def convert_to_markdown(schema_data: Dict[str, Any]) -> str:
    """
    Transforms the unified JSON schema blocks into human-readable Markdown format (e.g. lists, headers, tables).
    """
    logger.info("Converting normalized JSON schema elements to Markdown text")
    markdown_lines = []
    
    for page in schema_data.get("pages", []):
        markdown_lines.append(f"<!-- Page {page['page_number']} -->\n")
        for block in page.get("blocks", []):
            b_type = block.get("type")
            text = block.get("text", "")
            
            if b_type == "heading":
                markdown_lines.append(f"# {text}\n")
            elif b_type == "list":
                markdown_lines.append(f"- {text}\n")
            elif b_type == "table":
                # TODO: Convert structured JSON tables to Markdown format
                markdown_lines.append(f"\n{text}\n")
            else:
                markdown_lines.append(f"{text}\n")
        markdown_lines.append("\n")
        
    return "\n".join(markdown_lines)
