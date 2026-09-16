# DocScribe — Handoff Notes

> Working notes for picking this project back up. `ARCHITECTURE.md` is the source-of-truth design doc and has the full changelog with exact technical detail — this file is a status summary and a "what to check first" guide. Last updated 2026-09-16 (evening).

## Context-confirmation step (read first, new instance)

Before doing anything, confirm you understand:
1. Stack: React/Vite/TS/Tailwind v4 frontend, FastAPI backend, Celery+Redis queue, PostgreSQL, MinIO, Docker Compose, PaddleOCR PP-OCRv6 primary + Surya fallback (adaptive, confidence-threshold triggered).
2. Project path: `D:\Degree_GLS\sem 7\nexus-hack\ocr-pipeline` (Windows/PowerShell). Redis/Postgres run only in Docker.
3. `ARCHITECTURE.md` is the single source of truth — reconcile against it, don't just trust this file if they've drifted (this file drifted from reality at least twice already this project — see "Recurring failure modes" below).

---

## ✅ Phase 0 — MVP: closed, verified running end-to-end

Fine-tuned PP-OCRv6 loads and extracts real text, Surya fallback works, `/result`/`/jobs`/`/documents` endpoints are real DB+MinIO backed, spell-check (en/hi) is wired in. Full detail in `ARCHITECTURE.md`'s changelog.

## ✅ Phase 1 — Quality & Structure: closed, 8/8 items done and verified

| # | Item | Status | Verified how |
|---|---|---|---|
| 1 | Frontend proxy fix (`localhost` → `backend`) | ✅ Done | Real HTTP request through the proxy to a live backend endpoint |
| 2 | Model-loading singleton + named Docker volumes | ✅ Done | Confirmed singletons already correct; requirements split (backend 1.28GB vs worker 12GB, verified via `pip list`); named-volume persistence confirmed via two-cycle `down`/`up` with no re-download in logs |
| 3 | Reading order (column-clustering) | ✅ Done | Synthetic 2-column PDF, real API upload → correct Alpha-then-Beta block order, `avg_confidence: 0.9995` |
| 4 | Table detection (`TableRecognitionPipelineV2`) | ✅ Done (synthetic doc only) | Synthetic table+chart PDF, real API upload → table block with correct 4×3 rows/cols/cells/header/span schema. **No real-document table test has been run** — confirmed directly with the user, not just absent from logs. Worth doing if a real document with a table becomes available. |
| 5 | Figure/chart detection + MinIO crop | ✅ Done | Same synthetic test → figure block with `subtype:"chart"`, real 17KB PNG confirmed in MinIO via `head_object`, correct `needs_review` logic |
| 6 | Spell-check compound upgrade (`lookup_compound`) | ✅ Done | Real multi-word compound correction observed in the audit log against actual (badly-OCR'd) pipeline output, not a synthetic unit test |
| 7 | UI result display + JSON/Markdown download buttons | ✅ Done | Backend endpoints were already real from Phase 0; split one combined button into two explicit ones |
| 8 | Frontend document list (status badges, download/delete, error display) | ✅ Done | API-level: full loop (upload → status → download both formats → delete → confirmed gone from DB *and* MinIO *and* list) verified via direct API calls. Browser click-through: **confirmed manually by the user** in a follow-up session. |

### gu/mr spell-check dictionaries — sourced, and a real bug found and fixed along the way

Sourced from the Leipzig Corpora Collection (`guj_wikipedia_2021_100K`/`mar_wikipedia_2021_100K` — AI4Bharat's IndicNLP was checked first but only offers raw multi-hundred-MB text corpora, no ready frequency list), converted to the same format as en/hi's dictionaries, 184,172/162,970 entries after filtering out multi-word phrase entries that don't belong in a unigram dictionary.

**Found and fixed a real bug during verification, not just a sourcing gap**: SymSpell's `lookup_compound()` — the same function item 6 wired in for en/hi — shatters real, exactly-dictionary-matching Gujarati/Marathi words into meaningless fragments (confirmed: even at `max_edit_distance=0`, an exact dictionary word came back split into single characters). Isolated the cause to `lookup_compound()`'s own resegmentation search, not the dictionary — plain per-token `lookup()` on the identical dictionary matches every word correctly. gu/mr now use a per-token correction fallback (`_COMPOUND_UNSAFE_LANGS` in `spellcheck.py`) instead of compound correction; en/hi are unaffected and keep the compound upgrade. Verified against real Gujarati/Marathi paragraphs from the user's own multilingual sample PDF: real typo fixes work (`તડકિ`→`તડકો`, `आहि`→`आहे`), already-correct text is left untouched, numeric/ID protection holds.

### Two significant pre-existing bugs found and fixed while verifying the above (neither was part of the original ask — found because they were silently corrupting results)

1. **`preprocess.deskew_image()` angle-normalization bug.** OpenCV's `minAreaRect` angle convention differs between versions — the code only normalized the negative-angle case, so OpenCV 4.10.0 (what's actually installed) returned `+90°` for perfectly axis-aligned pages and the code rotated them anyway. This silently corrupted geometry (bboxes, reading order, everything downstream) on pages that were never actually skewed, and explains the "correcting skew of 90.00°" log lines seen on `sample.pdf` throughout Phase 0. Fixed with a symmetric normalization guard.
2. **`Block` DB table was missing columns its own ARCHITECTURE.md §11 ER diagram documented.** `subtype`/`table_data`/`image_url`/`caption`/`needs_review` never actually existed in the SQLAlchemy model or `init.sql` — the ER diagram was aspirational, not implemented. Added the columns (plus `jobs.error_message`, needed for item 8) to both the model and `init.sql`, and applied as a live `ALTER TABLE` against the running Postgres (no data loss).

### Recurring failure modes worth remembering

- **Doc-vs-code drift happens repeatedly on this project.** Three separate instances this session: (a) ARCHITECTURE.md claimed the fine-tuned model was loading fine when it wasn't (model directory got restructured underneath it), (b) the Block table's ER diagram was never actually implemented, (c) the original single `requirements.txt` didn't match what was actually needed per-service. **Always verify a documented claim against the running system before trusting it, especially after time has passed since the doc was last touched.**
- **A container being "Up and healthy" proves nothing about what code it's running.** Bind mounts (now added for `backend/app`, `backend/worker`, and `frontend/src`) mean a `docker compose restart <service>` is enough for pure source edits — no rebuild needed. A rebuild is still required for actual dependency changes (`requirements-*.txt`).
- **A plain-Python-edit image rebuild was observed re-running the Dockerfile's `apt-get` layer from a cold cache**, costing several minutes for zero actual reason. Prefer the bind-mount + restart loop over rebuilding unless dependencies changed.
- **Docker Desktop on Windows has a real bind-mount reconciliation bug**: repeatedly recreating a container against a compose mount whose *source path doesn't exist yet* can corrupt sibling files under that mount's parent directory (this is how `model/paddle/inference/`'s flat-format files got lost mid-session — since recovered: the model was correctly re-exported to `model/paddle/inference/rec_finetuned/`, which is now the canonical, working path; `model/paddle_old/` has been cleaned up and no longer exists). If a bind-mount source doesn't exist, create it (or fix the compose path) before the next `up`/`down` cycle — don't let Docker silently auto-vivify it.
- **CPU-only inference is slow enough to matter for planning.** A cold Surya run (model download + inference) took ~10 minutes; PaddleOCR + `TableRecognitionPipelineV2` together took ~2 minutes per page even with all models cached. Budget for this when testing — background the wait, don't block on it synchronously.

---

## Known gaps still open

- **No real-document table test.** Only the synthetic `table_test.pdf` has exercised item 4/5's table/figure code. Worth doing once a real document with a table is available.
- **gu/mr get per-token spell correction only**, not the compound split/merge correction en/hi have — `lookup_compound()` doesn't work safely for these two dictionaries (see above). Revisit if symspellpy's behavior here is better understood later, or if an alternative compound-correction approach is found.
- `en`/`hi`/`gu`/`mr` dictionaries are all subtitle- or Wikipedia-derived (colloquial/encyclopedic) — won't catch domain vocabulary (invoice/legal/medical terms). This was directly visible in the table-detection test: OCR read "Q1 Sales" as "G1 Sales" and similar — a font/rendering domain mismatch with the fine-tuned model on synthetic vector-rendered text, unrelated to spell-check or the structural correctness of the table/figure code.
- Surya fine-tuning deprioritized; stock weights only.
- Active language scope is 4 (en/hi/gu/mr); ta/te/bn/kn remain roadmap-only for both OCR and spell-check.
- Table/figure detection currently runs unconditionally per page (not confidence-gated) — this is correct per the architecture (tables/figures aren't a "low confidence" problem), but it does mean every page pays the `TableRecognitionPipelineV2` cost even when there's no table on it. Not optimized; not blocking.
- The main OCR text pass and the table/figure structure pass both run independently over the same page, so a table's cell text and a figure's caption currently *also* appear as ordinary paragraph/heading blocks in the flat block list. De-duplicating text blocks that fall inside a detected table/figure region would be a reasonable follow-up, not done here (out of the scope that was asked for).
