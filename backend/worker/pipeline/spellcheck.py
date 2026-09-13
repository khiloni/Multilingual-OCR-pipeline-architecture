"""
spellcheck.py — Automated post-OCR spell correction for DocScribe.

No manual/human correction step. Runs entirely offline using per-language
SymSpell dictionaries built from word-frequency lists (e.g. hermitdave/
FrequencyWords). Current scope: en/hi/gu/mr only, matching the 4 languages
the fine-tuned PP-OCRv6_medium_rec model was trained on. ta/te/bn/kn are
deferred until those get fine-tuned in a later phase — dictionaries for
them exist in the same source and can be dropped in later with no code
change (see CURRENT_LANGS below).

Design goals:
  - Only touch LOW-confidence OCR tokens; never rewrite high-confidence text.
  - Never touch numerics, alphanumeric IDs, or short tokens (protects
    invoice numbers, dates, codes — critical for the compliance/data-entry
    use case).
  - Every correction is logged with original text, corrected text, edit
    distance, and dictionary hit — this is the audit trail, and it is what
    makes "we auto-corrected X" a defensible, inspectable claim rather than
    a black box.
  - Bonus signal: language-dictionary hit-rate can be compared across
    Hindi vs Marathi dictionaries to cross-check detect_langs() output on
    ambiguous Devanagari blocks.

Install:
    pip install symspellpy

Dictionary prep (one-time, offline):
    en/hi: downloaded from hermitdave/FrequencyWords (2018 subtitle corpus) —
    https://github.com/hermitdave/FrequencyWords/tree/master/content/2018/{lang}
    (en_50k.txt; hi only ships as hi_full.txt, no 50k-truncated version).

    gu/mr: hermitdave/FrequencyWords does NOT have Gujarati or Marathi at all
    (confirmed 404 on content/2018/gu and content/2018/mr) — the handoff notes
    that assumed otherwise were wrong. No dictionary files are wired in for
    these two yet; LanguageSpellChecker degrades safely when a dict file is
    missing (correction disabled for that language, text passed through
    unchanged), so this is a silent no-op rather than a crash. Find and add
    ./dictionaries/gu_freq.txt / mr_freq.txt (word<TAB or space>count format)
    from another source (e.g. Leipzig Corpora Collection at
    wortschatz-leipzig.de, IndicNLP corpora) to enable them — no code change
    needed beyond dropping the file in.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from symspellpy import SymSpell, Verbosity

logger = logging.getLogger(__name__)

DICT_DIR = Path(__file__).parent / "dictionaries"
MAX_EDIT_DISTANCE = 2
PREFIX_LENGTH = 7  # SymSpell indexing param; fine for short Indic/Latin tokens

# Active scope: matches the 4 languages the fine-tuned rec model covers.
# Extend with "ta", "te", "bn", "kn" once those get fine-tuned — the
# hermitdave/FrequencyWords source already has dictionaries for all 8,
# so extending later is just adding files + entries here, no code change.
CURRENT_LANGS = ("en", "hi", "gu", "mr")

# Tokens we never touch: pure numbers, IDs with digits+letters mixed,
# very short strings, currency/percent symbols attached.
_SKIP_TOKEN_RE = re.compile(r"^[\d\W]+$|.*\d.*")


@dataclass
class Correction:
    original: str
    corrected: str
    edit_distance: int
    lang: str


@dataclass
class SpellCheckResult:
    text: str                       # corrected text
    corrections: List[Correction] = field(default_factory=list)
    dict_hit_rate: float = 0.0      # fraction of checkable tokens found valid


class LanguageSpellChecker:
    """One SymSpell instance per language. Lazily loaded, cached."""

    _instances: Dict[str, "LanguageSpellChecker"] = {}

    def __init__(self, lang: str):
        self.lang = lang
        self.sym_spell = SymSpell(
            max_dictionary_edit_distance=MAX_EDIT_DISTANCE,
            prefix_length=PREFIX_LENGTH,
        )
        dict_path = DICT_DIR / f"{lang}_freq.txt"
        if not dict_path.exists():
            logger.warning(
                "No frequency dictionary for lang=%s at %s — "
                "spell correction disabled for this language.",
                lang, dict_path,
            )
            self.available = False
            return
        loaded = self.sym_spell.load_dictionary(
            str(dict_path), term_index=0, count_index=1, encoding="utf-8"
        )
        self.available = loaded
        if not loaded:
            logger.warning("Failed to load dictionary for lang=%s", lang)

    @classmethod
    def get(cls, lang: str) -> "LanguageSpellChecker":
        if lang not in cls._instances:
            cls._instances[lang] = cls(lang)
        return cls._instances[lang]

    def is_known(self, token: str) -> bool:
        if not self.available:
            return True  # can't judge — don't flag as wrong
        hits = self.sym_spell.lookup(
            token, Verbosity.TOP, max_edit_distance=0
        )
        return len(hits) > 0

    def suggest(self, token: str) -> Optional[Correction]:
        if not self.available:
            return None
        suggestions = self.sym_spell.lookup(
            token, Verbosity.CLOSEST, max_edit_distance=MAX_EDIT_DISTANCE
        )
        if not suggestions:
            return None
        best = suggestions[0]
        if best.term == token:
            return None
        return Correction(
            original=token,
            corrected=best.term,
            edit_distance=best.distance,
            lang=self.lang,
        )


def _tokenize(text: str) -> List[str]:
    # Whitespace tokenization is sufficient for most of these scripts;
    # Indic scripts don't use spaces mid-word the way agglutinative
    # languages do, so this holds up better than it would for e.g. Thai.
    return text.split()


def correct_block_text(
    text: str,
    lang: str,
    token_confidences: Optional[List[float]] = None,
    confidence_threshold: float = 0.90,
) -> SpellCheckResult:
    """
    Correct a single OCR text block.

    Args:
        text: raw OCR text for the block/line.
        lang: ISO code matching a loaded dictionary (en/hi/gu/mr/ta/te/bn/kn).
        token_confidences: optional per-token OCR confidence, same length
            as text.split(). If omitted, every eligible token is checked.
        confidence_threshold: tokens at/above this are left untouched.

    Returns:
        SpellCheckResult with corrected text and a full correction log.
    """
    checker = LanguageSpellChecker.get(lang)
    tokens = _tokenize(text)
    out_tokens: List[str] = []
    corrections: List[Correction] = []
    checkable = 0
    known = 0

    for i, tok in enumerate(tokens):
        conf = token_confidences[i] if token_confidences and i < len(token_confidences) else 0.0
        eligible = (
            checker.available
            and len(tok) >= 3
            and not _SKIP_TOKEN_RE.match(tok)
            and conf < confidence_threshold
        )
        if not eligible:
            out_tokens.append(tok)
            continue

        checkable += 1
        if checker.is_known(tok):
            known += 1
            out_tokens.append(tok)
            continue

        fix = checker.suggest(tok)
        if fix is not None:
            out_tokens.append(fix.corrected)
            corrections.append(fix)
        else:
            out_tokens.append(tok)  # no confident suggestion — leave as-is

    hit_rate = (known / checkable) if checkable else 1.0
    return SpellCheckResult(
        text=" ".join(out_tokens),
        corrections=corrections,
        dict_hit_rate=hit_rate,
    )


def disambiguate_devanagari(text: str, candidates: List[str] = ("hi", "mr")) -> Dict[str, float]:
    """
    Cross-check candidate languages for an ambiguous Devanagari block by
    comparing dictionary hit-rates. Higher hit_rate = better lexical fit.
    Use alongside detect_langs() ranked candidates, not as a replacement —
    this catches cases where langid confidence is low but the vocabulary
    itself is a clear signal.
    """
    scores = {}
    for lang in candidates:
        result = correct_block_text(text, lang, confidence_threshold=1.0)  # check every token
        scores[lang] = result.dict_hit_rate
    return scores
