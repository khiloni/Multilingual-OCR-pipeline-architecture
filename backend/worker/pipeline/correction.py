# Purpose: API-based OCR/spell correction (Phase 2 item 0b), replacing the
# offline SymSpell path (spellcheck.py, kept on disk but no longer called
# from the default flow — see CORRECTION_PROVIDER="legacy_symspell").
#
# Why: SymSpell had no gu/mr dictionaries worth trusting and produced
# unreliable per-token fixes (see ARCHITECTURE.md changelog — lookup_compound()
# shattered real words for gu/mr). An LLM correction pass handles all four
# languages uniformly and can use sentence-level context a dictionary can't.
#
# Design:
#   - CorrectionProvider interface (ABC) — the active backend is an env
#     choice (CORRECTION_PROVIDER), not hardcoded, so swapping providers
#     is a config change, not a code change.
#   - ONE batched API call per PAGE (block_id + text + language), not per
#     block/word — bounds latency and cost.
#   - Strict system prompt: fix OCR/spelling only, never translate, keep
#     script/numbers/dates/names/codes/punctuation/line structure, no
#     added/removed content, JSON response with the same block ids.
#   - Safety rails so output needs no human recheck:
#       * response validated (same ids, valid JSON, non-empty) — any
#         mismatch means that block falls back to raw OCR text;
#       * a correction whose normalized edit distance from the original
#         exceeds CORRECTION_MAX_EDIT_DISTANCE_RATIO is rejected as a
#         rewrite, not a fix — raw text kept;
#       * table numeric cells, figure blocks, and blocks at/above
#         CORRECTION_SKIP_CONFIDENCE are never sent to the API at all;
#       * ANY failure (missing key, timeout, rate limit, malformed
#         response, network error) is caught, logged, and the page's raw
#         OCR text is used — this function never raises and never blocks
#         the pipeline (ARCHITECTURE.md's own hard-won lesson: an uncaught
#         Surya init error once zeroed out avg_confidence for an entire job).
#   - In-process cache keyed on hash(text, language) — a reprocessed
#     document, or a page whose blocks repeat the same short strings,
#     skips a redundant API call.

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an OCR post-processing corrector. You will receive a JSON array of text blocks extracted by OCR from a document page, each with a block id, the raw OCR text, and its language (ISO 639-1: en, hi, mr, or gu).

Your ONLY job: fix OCR recognition errors and spelling mistakes in each block's text.

Strict rules — violating any of these makes your output unusable:
1. Fix OCR/spelling errors only. Do not rewrite, rephrase, summarize, or improve style.
2. NEVER translate. Keep every block in its original language and original script exactly (Devanagari stays Devanagari, Gujarati stays Gujarati, Latin stays Latin).
3. Preserve numbers, dates, names, codes, IDs, and punctuation exactly as given, unless a digit/letter is an obvious OCR misread within a word (e.g. "wor1d" -> "world"). Never alter a number's value.
4. Preserve line structure and overall text length — do not merge or split blocks, do not add or remove sentences or content.
5. If a block's text looks already correct, return it completely unchanged.
6. If you are not confident about a fix, leave that part unchanged rather than guessing.

Output: respond with ONLY a JSON object of this exact shape, nothing else — no markdown fences, no commentary:
{"corrections": [{"block_id": "<id from input>", "corrected_text": "<corrected text>"}, ...]}

Include every block id from the input, in any order. Do not invent new ids."""

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CorrectionItem:
    block_id: str
    text: str
    language: str


@dataclass
class CorrectionResult:
    """Per-block outcome. `applied` is False whenever the raw text is being
    used for any reason — caller never needs to know *why*, only whether
    the stored corrected_text differs from original_text."""
    original_text: str
    corrected_text: str
    applied: bool
    reason: str = ""  # "ok" | "skipped_high_confidence" | "skipped_type" |
                       # "api_error" | "invalid_response" | "edit_distance_rejected" |
                       # "cache_hit" | "provider_disabled"


class CorrectionError(Exception):
    """Raised by a provider's correct_batch() on any failure; always caught
    by correct_page_blocks(), never escapes this module."""


class CorrectionProvider(ABC):
    @abstractmethod
    def correct_batch(self, items: List[CorrectionItem]) -> Dict[str, str]:
        """
        Returns {block_id: corrected_text} for ids it could confidently
        correct. An id missing from the result is treated as "leave
        unchanged" by the caller — a provider is not required to return
        every id, though the prompt asks for it.

        Must raise CorrectionError (not a provider-specific exception) on
        any failure, so the caller has one exception type to catch.
        """


# ---------------------------------------------------------------------------
# Anthropic provider (default)
# ---------------------------------------------------------------------------

class AnthropicCorrectionProvider(CorrectionProvider):
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise CorrectionError("anthropic package not installed") from exc
            if not settings.CORRECTION_API_KEY:
                raise CorrectionError("CORRECTION_API_KEY is not set")
            self._client = anthropic.Anthropic(
                api_key=settings.CORRECTION_API_KEY,
                timeout=settings.CORRECTION_TIMEOUT_SECONDS,
                max_retries=settings.CORRECTION_MAX_RETRIES,
            )
        return self._client

    def correct_batch(self, items: List[CorrectionItem]) -> Dict[str, str]:
        import anthropic

        client = self._get_client()
        payload = [{"block_id": i.block_id, "language": i.language, "text": i.text} for i in items]

        try:
            response = client.messages.create(
                model=settings.CORRECTION_MODEL,
                max_tokens=16000,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
        except anthropic.APIError as exc:
            raise CorrectionError(f"Anthropic API error: {exc}") from exc
        except Exception as exc:  # network errors, timeouts not subclassing APIError
            raise CorrectionError(f"Anthropic request failed: {exc}") from exc

        if response.usage is not None:
            logger.info(
                "Correction API usage | provider=anthropic model=%s input_tokens=%d output_tokens=%d",
                settings.CORRECTION_MODEL, response.usage.input_tokens, response.usage.output_tokens,
            )

        text = "".join(block.text for block in response.content if block.type == "text")
        return _parse_corrections_json(text)


# ---------------------------------------------------------------------------
# OpenAI provider (alternative; same CorrectionProvider contract)
# ---------------------------------------------------------------------------

class OpenAICorrectionProvider(CorrectionProvider):
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as exc:
                raise CorrectionError("openai package not installed") from exc
            if not settings.CORRECTION_API_KEY:
                raise CorrectionError("CORRECTION_API_KEY is not set")
            self._client = openai.OpenAI(
                api_key=settings.CORRECTION_API_KEY,
                timeout=settings.CORRECTION_TIMEOUT_SECONDS,
                max_retries=settings.CORRECTION_MAX_RETRIES,
            )
        return self._client

    def correct_batch(self, items: List[CorrectionItem]) -> Dict[str, str]:
        import openai

        client = self._get_client()
        payload = [{"block_id": i.block_id, "language": i.language, "text": i.text} for i in items]

        try:
            response = client.chat.completions.create(
                model=settings.CORRECTION_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
            )
        except openai.APIError as exc:
            raise CorrectionError(f"OpenAI API error: {exc}") from exc
        except Exception as exc:
            raise CorrectionError(f"OpenAI request failed: {exc}") from exc

        text = response.choices[0].message.content or ""
        return _parse_corrections_json(text)


# ---------------------------------------------------------------------------
# Gemini provider (alternative; same CorrectionProvider contract)
#
# Gemini's free tier caps gemini-3.8-flash at a handful of requests/minute
# (observed: generate_content_free_tier_requests quota, limit 5/min) — far
# tighter than Anthropic/OpenAI have shown. Two complementary mechanisms
# keep correction usable under that ceiling instead of burning the whole
# retry budget on 429s:
#   1. A proactive, process-wide rate limiter (_wait_for_rate_limit) spaces
#      every Gemini call at least 60/CORRECTION_RPM seconds apart, shared
#      across all jobs/pages in this worker process — not per call site.
#   2. On an actual 429, the server's own exact RetryInfo.retryDelay is
#      parsed from the error payload and waited out precisely, and that
#      wait does NOT count against GEMINI_RETRY_ATTEMPTS (which is reserved
#      for genuine 5xx/network failures) — a capped number of such waits
#      (_MAX_RATE_LIMIT_WAITS) still applies so a persistently exhausted
#      quota falls back to raw text instead of blocking the job forever.
# The SDK's own built-in retry is disabled (attempts=1) so this manual loop
# has sole control over when and how long to wait.
# ---------------------------------------------------------------------------

_rate_limit_lock = threading.Lock()
_last_correction_call_time: Optional[float] = None

# Safety cap on consecutive 429-triggered waits per call — if the quota is
# still exhausted after this many server-specified waits, give up and fall
# back to raw text rather than block the job indefinitely.
_MAX_RATE_LIMIT_WAITS = 3


def _wait_for_rate_limit() -> None:
    """Blocks until at least 60/CORRECTION_RPM seconds have passed since the
    last correction API call — module-level state, shared across every
    job/page processed by this worker, not scoped to one call site."""
    global _last_correction_call_time
    with _rate_limit_lock:
        min_interval = 60.0 / max(settings.CORRECTION_RPM, 1)
        now = time.monotonic()
        if _last_correction_call_time is not None:
            remaining = min_interval - (now - _last_correction_call_time)
            if remaining > 0:
                time.sleep(remaining)
        _last_correction_call_time = time.monotonic()


def _parse_retry_delay_seconds(error_details: Any) -> Optional[float]:
    """Extracts the server-specified wait (google.rpc.RetryInfo.retryDelay,
    e.g. "40s") from a Gemini error payload, if present. Returns None if the
    payload doesn't carry a structured RetryInfo (caller falls back to the
    configured max backoff)."""
    try:
        error_obj = error_details.get("error", error_details) if isinstance(error_details, dict) else {}
        for detail in error_obj.get("details") or []:
            if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("RetryInfo"):
                delay_str = str(detail.get("retryDelay", ""))
                if delay_str.endswith("s"):
                    return float(delay_str[:-1])
    except Exception:
        logger.debug("Could not parse retryDelay from Gemini error payload", exc_info=True)
    return None


class GeminiCorrectionProvider(CorrectionProvider):
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise CorrectionError("google-genai package not installed") from exc
            if not settings.CORRECTION_API_KEY:
                raise CorrectionError("CORRECTION_API_KEY is not set")
            from google.genai import types

            self._client = genai.Client(
                api_key=settings.CORRECTION_API_KEY,
                http_options=types.HttpOptions(
                    timeout=int(settings.CORRECTION_TIMEOUT_SECONDS * 1000),  # ms
                    retry_options=types.HttpRetryOptions(attempts=1),  # manual retry loop below
                ),
            )
        return self._client

    def correct_batch(self, items: List[CorrectionItem]) -> Dict[str, str]:
        from google.genai import types
        from google.genai import errors as genai_errors

        client = self._get_client()
        payload = [{"block_id": i.block_id, "language": i.language, "text": i.text} for i in items]
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
        )

        server_error_attempts = 0  # 5xx/network retries only
        rate_limit_waits = 0       # 429 waits — tracked separately, don't count above

        while True:
            _wait_for_rate_limit()
            try:
                response = client.models.generate_content(
                    model=settings.CORRECTION_MODEL,
                    contents=json.dumps(payload, ensure_ascii=False),
                    config=config,
                )
                break
            except genai_errors.ClientError as exc:
                if exc.code == 429 and rate_limit_waits < _MAX_RATE_LIMIT_WAITS:
                    delay = _parse_retry_delay_seconds(exc.details) or settings.GEMINI_RETRY_MAX_DELAY_SECONDS
                    if delay > settings.GEMINI_MAX_HONORED_RETRY_DELAY_SECONDS:
                        # A delay this long (observed: ~13.5h) means a daily
                        # quota is exhausted, not the per-minute one — no
                        # amount of waiting inside this task recovers that.
                        # Give up on correction for this page now rather
                        # than sleep through it and hang the whole worker
                        # (concurrency=1) for hours.
                        logger.warning(
                            "Gemini rate-limited (429) with a %.0fs retryDelay — exceeds the %.0fs "
                            "honored cap (likely a daily, not per-minute, quota); falling back to "
                            "raw text instead of waiting",
                            delay, settings.GEMINI_MAX_HONORED_RETRY_DELAY_SECONDS,
                        )
                        raise CorrectionError(f"Gemini API error: {exc}") from exc
                    rate_limit_waits += 1
                    logger.warning(
                        "Gemini rate-limited (429) — waiting the server-specified %.1fs before "
                        "retrying (wait %d/%d, not counted against the %d-attempt retry budget)",
                        delay, rate_limit_waits, _MAX_RATE_LIMIT_WAITS, settings.GEMINI_RETRY_ATTEMPTS,
                    )
                    time.sleep(delay)
                    continue
                raise CorrectionError(f"Gemini API error: {exc}") from exc
            except genai_errors.ServerError as exc:
                server_error_attempts += 1
                if server_error_attempts >= settings.GEMINI_RETRY_ATTEMPTS:
                    raise CorrectionError(f"Gemini API error: {exc}") from exc
                delay = min(
                    settings.GEMINI_RETRY_INITIAL_DELAY_SECONDS * (2 ** (server_error_attempts - 1)),
                    settings.GEMINI_RETRY_MAX_DELAY_SECONDS,
                )
                logger.info(
                    "Gemini server error (attempt %d/%d) — retrying in %.1fs: %s",
                    server_error_attempts, settings.GEMINI_RETRY_ATTEMPTS, delay, exc,
                )
                time.sleep(delay)
                continue
            except Exception as exc:  # network errors, timeouts not subclassing APIError
                raise CorrectionError(f"Gemini request failed: {exc}") from exc

        if response.usage_metadata is not None:
            logger.info(
                "Correction API usage | provider=gemini model=%s input_tokens=%d output_tokens=%d",
                settings.CORRECTION_MODEL,
                response.usage_metadata.prompt_token_count or 0,
                response.usage_metadata.candidates_token_count or 0,
            )

        return _parse_corrections_json(response.text or "")


def _parse_corrections_json(text: str) -> Dict[str, str]:
    """Extracts {block_id: corrected_text} from a provider's raw response
    text. Tolerates a stray markdown fence around the JSON despite the
    prompt's instruction not to add one. Raises CorrectionError on any
    parse/shape failure — the caller treats that as "use raw text"."""
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise CorrectionError("No JSON object found in correction response")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise CorrectionError(f"Correction response was not valid JSON: {exc}") from exc

    corrections = parsed.get("corrections")
    if not isinstance(corrections, list):
        raise CorrectionError("Correction response missing a 'corrections' array")

    result: Dict[str, str] = {}
    for entry in corrections:
        if not isinstance(entry, dict):
            continue
        block_id = entry.get("block_id")
        corrected_text = entry.get("corrected_text")
        if isinstance(block_id, str) and isinstance(corrected_text, str):
            result[block_id] = corrected_text
    return result


# ---------------------------------------------------------------------------
# Provider registry + in-process cache
# ---------------------------------------------------------------------------

_PROVIDERS: Dict[str, Any] = {
    "anthropic": AnthropicCorrectionProvider,
    "openai": OpenAICorrectionProvider,
    "gemini": GeminiCorrectionProvider,
}

_provider_instance: Optional[CorrectionProvider] = None
_cache: Dict[str, str] = {}  # hash(text, lang) -> corrected_text, this worker process only


def _get_provider() -> Optional[CorrectionProvider]:
    global _provider_instance
    provider_name = settings.CORRECTION_PROVIDER
    if provider_name in ("none", "legacy_symspell"):
        return None
    if _provider_instance is None:
        cls = _PROVIDERS.get(provider_name)
        if cls is None:
            logger.warning(
                "Unknown CORRECTION_PROVIDER=%r — correction disabled, raw OCR text will be used",
                provider_name,
            )
            return None
        _provider_instance = cls()
        logger.info("Initialised correction provider: %s (model=%s)", provider_name, settings.CORRECTION_MODEL)
    return _provider_instance


def _cache_key(text: str, language: str) -> str:
    return hashlib.sha256(f"{language}\x00{text}".encode("utf-8")).hexdigest()


def _normalized_edit_distance(a: str, b: str) -> float:
    """Levenshtein distance normalized to [0, 1] by the longer string's
    length — used to reject a "correction" that is really a rewrite."""
    if a == b:
        return 0.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 1.0
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1] / max(la, lb)


# Block types whose text is never sent for correction — table cells are
# numeric/short-label heavy and already OCR'd by a separate pass; figures
# carry no OCR text at all.
_SKIP_CORRECTION_TYPES = frozenset({"table", "figure"})


def correct_page_blocks(blocks: List[Dict[str, Any]]) -> List[CorrectionResult]:
    """
    Runs the full correction stage for one page's text-bearing blocks in a
    SINGLE batched API call (not per-block). Returns one CorrectionResult
    per input block, in the same order.

    Never raises. On any failure (disabled provider, missing key, API
    error, timeout, invalid response) every block in the page falls back
    to CorrectionResult(applied=False, corrected_text=original_text) —
    the caller always gets a usable list back.
    """
    results: List[CorrectionResult] = [None] * len(blocks)  # type: ignore[list-item]
    eligible: List[int] = []  # indices into `blocks` that need an API call
    items: List[CorrectionItem] = []

    for idx, block in enumerate(blocks):
        text = block.get("text", "") or ""
        if block.get("type") in _SKIP_CORRECTION_TYPES or not text.strip():
            results[idx] = CorrectionResult(text, text, False, "skipped_type")
            continue
        if block.get("confidence", 0.0) >= settings.CORRECTION_SKIP_CONFIDENCE:
            results[idx] = CorrectionResult(text, text, False, "skipped_high_confidence")
            continue

        lang = block.get("language") or "en"
        cache_key = _cache_key(text, lang)
        if cache_key in _cache:
            cached = _cache[cache_key]
            results[idx] = CorrectionResult(text, cached, cached != text, "cache_hit")
            continue

        eligible.append(idx)
        items.append(CorrectionItem(block_id=block.get("block_id") or f"idx{idx}", text=text, language=lang))

    if not items:
        return [r if r is not None else CorrectionResult("", "", False, "skipped_type") for r in results]

    provider = _get_provider()
    if provider is None:
        for idx, item in zip(eligible, items):
            results[idx] = CorrectionResult(item.text, item.text, False, "provider_disabled")
        return results  # type: ignore[return-value]

    try:
        corrected_map = provider.correct_batch(items)
    except CorrectionError as exc:
        logger.warning("Correction API call failed — using raw OCR text for this page: %s", exc)
        for idx, item in zip(eligible, items):
            results[idx] = CorrectionResult(item.text, item.text, False, "api_error")
        return results  # type: ignore[return-value]
    except Exception:
        # Defensive: a provider bug must never crash the pipeline either.
        logger.exception("Unexpected error in correction provider — using raw OCR text for this page")
        for idx, item in zip(eligible, items):
            results[idx] = CorrectionResult(item.text, item.text, False, "api_error")
        return results  # type: ignore[return-value]

    for idx, item in zip(eligible, items):
        corrected = corrected_map.get(item.block_id)
        if corrected is None:
            results[idx] = CorrectionResult(item.text, item.text, False, "invalid_response")
            continue
        if corrected == item.text:
            results[idx] = CorrectionResult(item.text, item.text, False, "ok")
            _cache[_cache_key(item.text, item.language)] = corrected
            continue
        distance_ratio = _normalized_edit_distance(item.text, corrected)
        if distance_ratio > settings.CORRECTION_MAX_EDIT_DISTANCE_RATIO:
            logger.info(
                "Rejecting correction for block %s: edit distance ratio %.2f exceeds %.2f (looks like a rewrite)",
                item.block_id, distance_ratio, settings.CORRECTION_MAX_EDIT_DISTANCE_RATIO,
            )
            results[idx] = CorrectionResult(item.text, item.text, False, "edit_distance_rejected")
            continue
        results[idx] = CorrectionResult(item.text, corrected, True, "ok")
        _cache[_cache_key(item.text, item.language)] = corrected

    return results  # type: ignore[return-value]
