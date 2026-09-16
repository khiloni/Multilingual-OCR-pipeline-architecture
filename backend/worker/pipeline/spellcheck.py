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

Compound correction (Phase 1 item 6):
  Uses SymSpell's lookup_compound() at the run level (a contiguous span of
  eligible tokens within a block — see below) instead of per-token lookup(),
  so OCR segmentation errors (a word wrongly split into two, or two words
  wrongly merged) get fixed using surrounding-word context, not just
  single-token edit distance. Runs on the UNIGRAM dictionary only — no
  bigram frequency dictionary is loaded. Confirmed empirically (see
  ARCHITECTURE.md changelog) that lookup_compound() degrades gracefully
  without one: symspellpy's own bigram lookup is optional internally, so
  this is intentional "confirmed degraded unigram-only compound mode", not
  a missing feature.

  IMPORTANT interaction with the numeric/ID guardrail: lookup_compound()
  operates on a whole string, not a single token, and empirically will
  restructure whitespace around a protected token even with
  ignore_non_words=True — e.g. "INV-4521" becomes "INV 4521" (the hyphen
  is treated as a token boundary and silently replaced with a space). That
  would corrupt an ID's exact formatting even though its digits/letters
  are never *reworded*. To keep the "IDs are never touched" guarantee
  exact, correct_block_text() below segments each block's tokens into
  contiguous runs of eligible (checkable, low-confidence, non-numeric)
  tokens and only calls lookup_compound() on those runs — a protected
  token is never included in the string handed to SymSpell at all, so it
  cannot be reformatted.

Install:
    pip install symspellpy

Dictionary prep (one-time, offline):
    en/hi: downloaded from hermitdave/FrequencyWords (2018 subtitle corpus) —
    https://github.com/hermitdave/FrequencyWords/tree/master/content/2018/{lang}
    (en_50k.txt; hi only ships as hi_full.txt, no 50k-truncated version).

    gu/mr: hermitdave/FrequencyWords does NOT have Gujarati or Marathi at all
    (confirmed 404 on content/2018/gu and content/2018/mr). Resolved via the
    Leipzig Corpora Collection (wortschatz-leipzig.de) instead — AI4Bharat's
    IndicNLP corpora were checked first but are raw text corpora needing a
    separate tokenize+count step to become a frequency list, with no direct
    lightweight download; Leipzig ships pre-computed word-frequency lists as
    a plain download, so it was used instead. Downloaded
    guj_wikipedia_2021_100K / mar_wikipedia_2021_100K (100K-sentence
    Wikipedia-derived corpora, the largest size Leipzig offers for Gujarati;
    Marathi goes up to 300K but 100K was used for parity), converted their
    3-column `rank<TAB>word<TAB>count` -words.txt into the same 2-column
    `word count` format as en_freq.txt/hi_freq.txt — excluding entries where
    Leipzig's own "word" field is actually a multi-word proper-noun phrase
    (e.g. "United States") with an embedded space, which both breaks the
    2-column format and can never match a single OCR token anyway (2,111 /
    7,101 such entries excluded) — leaving 184,172 / 162,970 real single-word
    entries — see ARCHITECTURE.md changelog for the exact
    conversion and verification against real Gujarati/Marathi paragraphs
    from a user-supplied multilingual sample PDF).
"""

from __future__ import annotations

import difflib
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
# All 4 now have real dictionary files (en/hi from hermitdave/FrequencyWords,
# gu/mr from Leipzig Corpora — see module docstring). Extend with "ta",
# "te", "bn", "kn" once those get fine-tuned; hermitdave/FrequencyWords has
# dictionaries for those four, so that extension is a files-plus-this-tuple
# change — but gu/mr specifically needed Leipzig, not hermitdave, so don't
# assume the same source works for every future language without checking.
CURRENT_LANGS = ("en", "hi", "gu", "mr")

# Languages where SymSpell's lookup_compound() produces GARBAGE, not just
# imperfect results — confirmed empirically against real Gujarati/Marathi
# text from a user-supplied sample PDF (see ARCHITECTURE.md changelog):
# even at max_edit_distance=0, lookup_compound() shatters exact dictionary
# words into meaningless single/double-character fragments (e.g. "તેઓ"
# an exact dictionary entry, freq 3819 — became "ત ઓ"). Isolated the cause
# to lookup_compound()'s own resegmentation search specifically: plain
# per-token lookup() on the exact same dictionary correctly matches every
# word (distance=0, sane frequencies). Root cause looks like symspellpy's
# compound algorithm not generalizing well to scripts/dictionaries with
# many short, extremely-high-frequency function words (Gujarati/Marathi
# have far more 1-2 character standalone dictionary entries, proportionally,
# than the en/hi lists) — removing 1-character entries did NOT fix it, so
# it's not simply "the dictionary has too much junk"; this is a real
# lookup_compound() limitation for these two languages specifically, not a
# dictionary-quality problem. en/hi were independently verified earlier
# this session with real compound-error fixes in the audit log and are
# NOT affected — only gu/mr fall back to per-token correction (see
# correct_block_text() below), losing the split/merge-fixing benefit of
# item 6's compound upgrade for these two languages until symspellpy's
# behavior here is understood better or an alternative library is used.
_COMPOUND_UNSAFE_LANGS = frozenset({"gu", "mr"})

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

    def suggest_compound(self, text: str) -> Optional[str]:
        """
        Runs SymSpell's compound (multi-word) correction over `text` and
        returns the corrected string, or None if unavailable/unchanged.

        Unlike suggest(), this can fix split/merged-word segmentation
        errors using neighboring-word context — the whole point of using
        lookup_compound() over per-token lookup() (item 6). Caller is
        responsible for only ever passing already-eligible text (see the
        run-segmentation in correct_block_text) since this method has no
        per-token confidence/skip awareness of its own.
        """
        if not self.available:
            return None
        suggestions = self.sym_spell.lookup_compound(
            text, max_edit_distance=MAX_EDIT_DISTANCE, ignore_non_words=True
        )
        if not suggestions:
            return None
        corrected = suggestions[0].term
        return corrected if corrected != text else None


def _tokenize(text: str) -> List[str]:
    # Whitespace tokenization is sufficient for most of these scripts;
    # Indic scripts don't use spaces mid-word the way agglutinative
    # languages do, so this holds up better than it would for e.g. Thai.
    return text.split()


def _span_edit_distance(a: str, b: str) -> int:
    """Plain Levenshtein distance between two short strings (correction-log display only)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (ca != cb),
            )
        prev = cur
    return prev[-1]


def correct_block_text(
    text: str,
    lang: str,
    token_confidences: Optional[List[float]] = None,
    confidence_threshold: float = 0.90,
) -> SpellCheckResult:
    """
    Correct a single OCR text block using compound (multi-word) lookup.

    Args:
        text: raw OCR text for the block/line.
        lang: ISO code matching a loaded dictionary (en/hi/gu/mr/ta/te/bn/kn).
        token_confidences: optional per-token OCR confidence, same length
            as text.split(). If omitted, every eligible token is checked.
        confidence_threshold: tokens at/above this are left untouched.

    Returns:
        SpellCheckResult with corrected text and a full correction log.

    Tokens are segmented into contiguous runs of "eligible" (checkable,
    low-confidence, non-numeric) tokens; lookup_compound() runs once per
    run rather than once per token, so it can fix split/merged-word errors
    using neighboring words in that run. Ineligible tokens (numerics, IDs,
    high-confidence, too-short) are spliced back verbatim at their original
    position and are never passed into lookup_compound() at all — see the
    module docstring for why that matters (compound lookup can silently
    reformat whitespace around a token it's merely "ignoring" the content of).
    """
    checker = LanguageSpellChecker.get(lang)
    tokens = _tokenize(text)
    if not tokens:
        return SpellCheckResult(text=text, corrections=[], dict_hit_rate=1.0)

    eligible_mask: List[bool] = []
    for i, tok in enumerate(tokens):
        conf = token_confidences[i] if token_confidences and i < len(token_confidences) else 0.0
        eligible_mask.append(
            checker.available
            and len(tok) >= 3
            and not _SKIP_TOKEN_RE.match(tok)
            and conf < confidence_threshold
        )

    if not any(eligible_mask):
        return SpellCheckResult(text=text, corrections=[], dict_hit_rate=1.0)

    out_pieces: List[str] = []
    corrections: List[Correction] = []
    checkable = 0
    known = 0

    i = 0
    while i < len(tokens):
        if not eligible_mask[i]:
            out_pieces.append(tokens[i])
            i += 1
            continue

        j = i
        while j < len(tokens) and eligible_mask[j]:
            j += 1
        run_tokens = tokens[i:j]
        run_text = " ".join(run_tokens)
        checkable += len(run_tokens)
        known += sum(1 for tok in run_tokens if checker.is_known(tok))

        if lang in _COMPOUND_UNSAFE_LANGS:
            # Per-token fallback — see _COMPOUND_UNSAFE_LANGS docstring.
            # No split/merge fixing, but each token is corrected (or left
            # alone) independently, so a known-good exact word can never
            # be shattered by a neighboring word's resegmentation search.
            corrected_run_tokens = []
            for tok in run_tokens:
                fix = checker.suggest(tok)
                if fix is not None:
                    corrected_run_tokens.append(fix.corrected)
                    corrections.append(fix)
                else:
                    corrected_run_tokens.append(tok)
            out_pieces.append(" ".join(corrected_run_tokens))
            i = j
            continue

        corrected_text = checker.suggest_compound(run_text)
        if corrected_text is None:
            out_pieces.append(run_text)
            i = j
            continue

        corrected_tokens = corrected_text.split()
        matcher = difflib.SequenceMatcher(a=run_tokens, b=corrected_tokens, autojunk=False)
        for tag, a0, a1, b0, b1 in matcher.get_opcodes():
            if tag == "equal":
                continue
            orig_span = " ".join(run_tokens[a0:a1])
            corrected_span = " ".join(corrected_tokens[b0:b1])
            corrections.append(
                Correction(
                    original=orig_span,
                    corrected=corrected_span,
                    edit_distance=_span_edit_distance(orig_span, corrected_span),
                    lang=lang,
                )
            )
        out_pieces.append(corrected_text)
        i = j

    hit_rate = (known / checkable) if checkable else 1.0
    return SpellCheckResult(
        text=" ".join(out_pieces),
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
