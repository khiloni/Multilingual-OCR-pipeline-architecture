# Purpose: Compares Character Error Rate (CER) and Word Error Rate (WER) of
# the deployed fine-tuned PaddleOCR recognition model against stock
# PP-OCRv6_medium_rec, per language, on a validation set of (cropped line
# image, ground truth text) pairs. Writes a per-sample comparison.csv and a
# per-language summary.csv.
#
# Must run inside the worker container (needs paddleocr/paddlepaddle):
#   docker compose exec worker python evaluation/scripts/run_benchmark.py \
#       --val-list evaluation/recognition/val_list.txt
#
# val_list.txt format: one sample per line, tab-separated:
#   <image_path>\t<language>\t<ground_truth_text>
# image_path is relative to the val_list.txt file's own directory.
# language is one of: en, hi, mr, gu
#
# This benchmark's shipped validation set (evaluation/recognition/images/) is
# synthetic (rendered text, not real scans) — see evaluation/recognition's
# section in README.md.

import argparse
import csv
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_FINETUNED_MODEL_DIR = os.environ.get(
    "PADDLEOCR_REC_MODEL_DIR", "/app/model/paddle/inference/rec_finetuned"
)
_STOCK_MODEL_NAME = "PP-OCRv6_medium_rec"
_SUPPORTED_LANGUAGES = ("en", "hi", "mr", "gu")


@dataclass
class Sample:
    image_path: Path
    language: str
    ground_truth: str


def _levenshtein(a: List[str], b: List[str]) -> int:
    """Classic O(len(a)*len(b)) edit-distance DP, operating on any sequence
    (characters for CER, whitespace-split tokens for WER)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                prev[j] + 1,        # deletion
                curr[j - 1] + 1,    # insertion
                prev[j - 1] + cost, # substitution
            )
        prev = curr
    return prev[-1]


def compute_cer(prediction: str, ground_truth: str) -> float:
    if not ground_truth:
        return 0.0 if not prediction else 1.0
    distance = _levenshtein(list(prediction), list(ground_truth))
    return distance / len(ground_truth)


def compute_wer(prediction: str, ground_truth: str) -> float:
    """Word-level edit distance on whitespace-split tokens. Approximation for
    Devanagari/Gujarati, which don't reliably delimit words with whitespace
    the way Latin scripts do — treat WER on hi/mr/gu as a rough signal, CER
    as the primary metric for those languages."""
    gt_tokens = ground_truth.split()
    pred_tokens = prediction.split()
    if not gt_tokens:
        return 0.0 if not pred_tokens else 1.0
    distance = _levenshtein(pred_tokens, gt_tokens)
    return distance / len(gt_tokens)


def load_val_list(val_list_path: Path) -> List[Sample]:
    samples = []
    base_dir = val_list_path.parent
    with open(val_list_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                logger.warning(
                    "val_list.txt line %d: expected 3 tab-separated fields, got %d — skipping",
                    line_num, len(parts),
                )
                continue
            rel_path, language, ground_truth = parts
            if language not in _SUPPORTED_LANGUAGES:
                logger.warning(
                    "val_list.txt line %d: unsupported language '%s' — skipping",
                    line_num, language,
                )
                continue
            samples.append(Sample(base_dir / rel_path, language, ground_truth))
    return samples


def _load_image(path: Path):
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Could not decode image: {path}")
    return image


def run_recognizer(model_dir: Optional[str], samples: List[Sample]) -> List[str]:
    """Returns one prediction string per sample, in the same order."""
    from paddleocr import TextRecognition

    kwargs = {"model_name": _STOCK_MODEL_NAME}
    if model_dir:
        kwargs["model_dir"] = model_dir
    recognizer = TextRecognition(**kwargs)

    predictions = []
    for sample in samples:
        try:
            image = _load_image(sample.image_path)
            result = next(iter(recognizer.predict(image)))
            predictions.append(result["rec_text"])
        except Exception:
            logger.error("Failed on sample %s", sample.image_path, exc_info=True)
            predictions.append("")
    return predictions


def write_comparison_csv(
    output_path: Path, samples: List[Sample], v5_predictions: List[str], stock_predictions: List[str]
) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["image", "language", "actual_text", "v5_prediction", "stock_prediction", "cer_v5"])
        for sample, v5_pred, stock_pred in zip(samples, v5_predictions, stock_predictions):
            cer = compute_cer(v5_pred, sample.ground_truth)
            writer.writerow([
                str(sample.image_path.relative_to(sample.image_path.parents[1])),
                sample.language, sample.ground_truth, v5_pred, stock_pred, f"{cer:.4f}",
            ])


def write_summary_csv(
    output_path: Path, samples: List[Sample], v5_predictions: List[str], stock_predictions: List[str]
) -> Dict[str, Dict[str, float]]:
    by_lang: Dict[str, List[int]] = {}
    for i, sample in enumerate(samples):
        by_lang.setdefault(sample.language, []).append(i)

    summary: Dict[str, Dict[str, float]] = {}
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["language", "model", "n_samples", "avg_cer", "avg_wer"])
        for lang in sorted(by_lang):
            indices = by_lang[lang]
            for model_name, predictions in (("v5", v5_predictions), ("stock", stock_predictions)):
                cers = [compute_cer(predictions[i], samples[i].ground_truth) for i in indices]
                wers = [compute_wer(predictions[i], samples[i].ground_truth) for i in indices]
                avg_cer, avg_wer = sum(cers) / len(cers), sum(wers) / len(wers)
                writer.writerow([lang, model_name, len(indices), f"{avg_cer:.4f}", f"{avg_wer:.4f}"])
                summary.setdefault(lang, {})[model_name] = {"avg_cer": avg_cer, "avg_wer": avg_wer, "n": len(indices)}
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--val-list", type=Path, default=Path("evaluation/recognition/val_list.txt"))
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/recognition"))
    args = parser.parse_args()

    if not args.val_list.exists():
        logger.error("Validation list not found: %s", args.val_list)
        return 1

    samples = load_val_list(args.val_list)
    if not samples:
        logger.error("No valid samples loaded from %s", args.val_list)
        return 1
    logger.info("Loaded %d validation samples", len(samples))

    logger.info("Running deployed V5 model (PADDLEOCR_REC_MODEL_DIR=%s)...", _FINETUNED_MODEL_DIR)
    v5_predictions = run_recognizer(_FINETUNED_MODEL_DIR, samples)

    logger.info("Running stock model (%s)...", _STOCK_MODEL_NAME)
    stock_predictions = run_recognizer(None, samples)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = args.output_dir / "comparison.csv"
    summary_path = args.output_dir / "summary.csv"

    write_comparison_csv(comparison_path, samples, v5_predictions, stock_predictions)
    summary = write_summary_csv(summary_path, samples, v5_predictions, stock_predictions)

    logger.info("Wrote %s and %s", comparison_path, summary_path)
    for lang, models in sorted(summary.items()):
        v5, stock = models["v5"], models["stock"]
        logger.info(
            "%s: v5 CER=%.4f WER=%.4f (n=%d) | stock CER=%.4f WER=%.4f (n=%d)",
            lang, v5["avg_cer"], v5["avg_wer"], v5["n"], stock["avg_cer"], stock["avg_wer"], stock["n"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
