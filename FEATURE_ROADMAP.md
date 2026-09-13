# DocScribe — Feature Roadmap

**Companion to:** `ARCHITECTURE.md` (Section 4 summary table links here for full detail)
**Last updated:** 2026-08-30

> This file tracks feature-level progress phase by phase. Check items off as they're implemented and verified — not just coded. Update the Status column at the phase level as work progresses.

---

## Phase 0 — MVP

**Goal:** Prove the core loop works end to end — upload a PDF, get text back.
**Status:** In Progress (skeleton complete, business logic pending)

- [ ] PDF upload endpoint accepts multi-page files with validation
- [ ] Page-level text extraction (PaddleOCR only, no fallback routing yet)
- [ ] Per-page language detection
- [ ] Basic text cleanup: dehyphenation, whitespace normalization, encoding fixes
- [ ] Job status flow: `queued → processing → done` (with `failed` on error)
- [ ] Download results as `.txt` / `.json`
- [ ] Frontend: upload form, job status polling, basic result view

---

## Phase 1 — Quality & Structure

**Goal:** Move from "text blob" to structured, trustworthy output.
**Status:** Planned

- [ ] Layout-aware extraction: reading order, headings, paragraphs preserved
- [ ] Table extraction as structured JSON (rows/cols/cells/spans) — see `ARCHITECTURE.md` §10.2
- [ ] Per-block confidence scoring
- [ ] Markdown export derived from JSON (not a separate extraction path)
- [ ] Async batch processing — multiple PDFs queued and processed independently
- [ ] Low-confidence block/page flagging surfaced in the UI

---

## Phase 2 — Multilingual & Robustness

**Goal:** Handle real-world messy, mixed-language, low-quality scans.
**Status:** Planned

- [ ] Multi-script support: Latin, Devanagari, CJK, Arabic/RTL
- [ ] Mixed-language-per-page and mixed-language-per-block handling
- [ ] Original-language storage policy enforced (no auto-translation) — see `ARCHITECTURE.md` §10.1
- [ ] Deskew / denoise preprocessing (OpenCV) before OCR
- [ ] Confidence-based fallback routing: PaddleOCR → Surya on low confidence / complex layout
- [ ] Image/figure region detection, cropping, and storage — see `ARCHITECTURE.md` §10.3
- [ ] Chart regions flagged `needs_review`, no fabricated data-value extraction

---

## Phase 3 — Downstream Integration

**Goal:** Make DocScribe's output consumable by external systems, not just viewable in-app.
**Status:** Planned

- [ ] Public REST API for external consumers (stable contract, versioned)
- [ ] Webhooks on job completion
- [ ] PII / compliance field flagging
- [ ] Audit trail: who/what/when for every extraction, tied to original file hash
- [ ] Optional translation layer (additive `translated_text` fields, opt-in per job)
- [ ] Search index push (OpenSearch/Elasticsearch) with per-language analyzers

---

## Phase 4 — Ops & Scale

**Goal:** Operate this as an ongoing service rather than a one-off demo.
**Status:** Backlog

- [ ] Metrics dashboard: job volume, avg confidence trend, per-language stats
- [ ] Human-in-the-loop correction UI with feedback capture
- [ ] Engine versioning and A/B comparison (swap PaddleOCR/Surya versions safely)
- [ ] Authentication + multi-tenant support
- [ ] Rate limiting
- [ ] Data retention policy enforcement (auto-purge per configurable window)

---

## Status Legend

| Status | Meaning |
|---|---|
| Backlog | Identified, not yet scheduled |
| Planned | Scoped, sequenced, not started |
| In Progress | Actively being built |
| Verified | Built and confirmed working end-to-end (not just compiles) |

---

## Change Log

| Date | Change |
|---|---|
| 2026-08-30 | Extracted from `ARCHITECTURE.md` Section 4 into standalone tracked roadmap |
