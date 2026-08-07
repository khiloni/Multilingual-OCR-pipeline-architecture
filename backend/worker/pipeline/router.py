# Purpose: Engine selection routing logic deciding between the fast PaddleOCR path and Surya VLM fallback path.
# Future TODOs: Add complex layout detection (scanned-noisy, multi-column), and script mix analysis classifiers.

import logging
from typing import List, Dict, Any
import numpy as np
from worker.pipeline.engines.paddle_engine import PaddleOCREngine
from worker.pipeline.engines.surya_engine import SuryaOCREngine
from app.core.config import settings

logger = logging.getLogger(__name__)

class EngineRouter:
    """
    Decides which OCR engine to execute based on confidence scoring and document complexity.
    """
    def __init__(self) -> None:
        self.paddle = PaddleOCREngine()
        self.surya = SuryaOCREngine()

    def process_page(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        Executes routing flow: Runs PaddleOCR, checks confidence/layout constraints, and triggers Surya if needed.
        """
        logger.info("Executing engine routing pipeline check for current page")
        
        # 1. Run PaddleOCR (fast path)
        paddle_blocks = self.paddle.extract_text(image)
        paddle_tables = self.paddle.extract_tables(image)
        
        # Evaluate confidence and layout triggers
        # Section 9 constraint check
        avg_confidence = np.mean([b["confidence"] for b in paddle_blocks]) if paddle_blocks else 0.0
        has_table = len(paddle_tables) > 0
        is_mixed_script = False  # TODO: Implement multi-script detection on paddle blocks
        
        confidence_threshold = settings.PADDLE_CONFIDENCE_THRESHOLD
        
        if avg_confidence >= confidence_threshold and not has_table and not is_mixed_script:
            logger.info(f"PaddleOCR path accepted with confidence {avg_confidence:.2f}")
            # Map type fields if missing and return
            for b in paddle_blocks:
                b["engine_used"] = "paddleocr"
            return paddle_blocks
            
        logger.warning(
            f"Routing trigger fired: avg_confidence={avg_confidence:.2f} (threshold={confidence_threshold}), "
            f"has_table={has_table}, mixed_script={is_mixed_script}. Falling back to Surya VLM."
        )
        
        # 2. Run Surya fallback path
        surya_blocks = self.surya.extract_text_and_layout(image)
        for b in surya_blocks:
            b["engine_used"] = "surya"
            
        # 3. Merge best-of-both by comparing block overlap or returning higher quality VLM output
        merged_blocks = self.merge_blocks(paddle_blocks, surya_blocks)
        return merged_blocks

    def merge_blocks(self, paddle_blocks: List[Dict[str, Any]], surya_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Combines and overlaps blocks based on confidence thresholds and positional bounding boxes.
        """
        logger.info("Merging block outputs from PaddleOCR and Surya paths")
        # TODO: Implement IOU/overlap box merging algorithms.
        # Fallback placeholder: Prefer Surya results if fallback was triggered
        return surya_blocks if surya_blocks else paddle_blocks
# Initialize a global router
router = EngineRouter()
