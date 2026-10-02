# DocScribe

**DocScribe** is a multilingual OCR pipeline for extracting clean, structured text from multi-page PDFs — scanned or born-digital. It combines a fine-tuned recognition model with a layout-aware fallback engine, producing structured JSON (text, bounding boxes, confidence, language, tables, figures) rather than a flat text blob, plus a set of derived exports.

---

## Features

- **Multilingual OCR** — English, Hindi, Marathi, Gujarati, via a fine-tuned recognition model (see below), with a layout-aware fallback for everything else.
- **Structured output** — text blocks with bounding boxes, per-block confidence, and language; tables as structured row/column/cell data; figures and charts cropped from scanned or born-digital pages alike and linked in the output, with captions matched where detectable.
- **Image-variant retry** — pages below the confidence/quality threshold are retried against contrast-enhanced and adaptive-threshold variants before falling back to a secondary engine; the winning attempt is recorded per page.
- **Explicit OCR quality score** — a 0–1 per-page score from five weighted signals, used alongside confidence to gate retries, not just reported.
- **API-based post-OCR correction** (optional, off by default) — garbled/low-confidence text corrected via a configurable LLM provider, with strict safety rails: never translates, never touches numbers/names/codes, rejects rewrite-sized edits, never crashes the pipeline on API failure.
- **Full-text search** — PostgreSQL full-text search across every OCR'd page, no additional service.
- **Six export formats** — JSON (canonical), Markdown, TXT, Searchable PDF, Highlighted PDF, Structured PDF — see the table below.
- **Document management UI** — upload, track status, search, and download results from a single dashboard.

## How to run

```bash
docker compose up --build
```

- Frontend: [http://localhost:5173](http://localhost:5173)
- Backend API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- MinIO console: [http://localhost:9001](http://localhost:9001)

Copy `.env.example` to `.env` first. `docker-compose.yml` loads it via `env_file` for both `backend` and `worker` — after editing `.env`, recreate the affected service (`docker compose up -d worker`) rather than `restart`, since `restart` does not reload `env_file`.

Full API contract and system design: [`ARCHITECTURE.md`](./ARCHITECTURE.md).

### Download formats

| Format | What it is |
|---|---|
| `json` | Canonical structured output — source of truth for every other format |
| `markdown` | Headings/tables/figures rendered as Markdown |
| `txt` | Plain text, page-by-page, in reading order |
| `searchable_pdf` | Original page image with an invisible, searchable text layer |
| `highlighted_pdf` | Same, plus a color overlay per block keyed to detected language, with a legend |
| `structured_pdf` | Clean reconstruction from scratch — text, tables, and figures redrawn at their position, no background scan |

## Project Structure

```
ocr-pipeline/
├── backend/        # FastAPI app (API) + Celery worker (OCR pipeline, pipeline/ subpackage)
├── frontend/        # React + Vite + TypeScript + Tailwind dashboard
├── infra/           # PostgreSQL init SQL
├── model/           # Deployed fine-tuned recognition model (inference export)
├── evaluation/       # Recognition benchmark, real-PDF outputs, and test/benchmark scripts
├── README.md
├── ARCHITECTURE.md   # System design, diagrams, DB schema, full API contract
├── docker-compose.yml
├── .env.example
└── .gitignore
```

## Fine-Tuned Recognition Model

DocScribe's primary engine uses a fine-tuned PaddleOCR PP-OCRv6 recognition model for **English, Hindi, Marathi, and Gujarati** — the currently deployed version is **V5**.

- **Training data:** synthetic text lines rendered across **23 font families**
- **Dictionary:** 660 characters (Latin, Devanagari, Gujarati, symbols)
- **Training:** 20 epochs, batch size 8, learning rate 0.0005, fine-tuned from the pretrained PP-OCRv6 recognition checkpoint

### Model Training Timeline

| Version | Training setup | Languages | Dictionary / Characters | Epochs | Batch Size | Learning Rate | Result / Status |
|---|---|---:|---:|---:|---:|---:|---|
| V1 | Default pretrained model | 8 | ~38K characters | 10 | 6 | 0.0005 | Best accuracy 90.26%, but showed incorrect predictions; Kannada relatively good, Bengali weaker/biased |
| V2 | Fine-tuned on custom dataset | 8 | ~171K characters | 15 | 6 | 0.0005 | Best accuracy 98.25%, normalized edit similarity ~99.50%; predictions still sometimes wrong |
| V3 | Fine-tuned on default/pretrained model | 8 | ~70K dictionary characters | 20 planned | 8 | 0.0003 | Halted/cancelled; not used as final model |
| V4 | Fresh fine-tuning from original PP-OCRv6 pretrained checkpoint | 4 | 326 dictionary chars; 287 used; 0 missing | 20 | 8 | 0.0003 | 60.18% validation accuracy, 97.29% edit similarity |
| **V5** | **Final configuration (deployed)** | **4** | **660-character dictionary** | **20** | **8** | **0.0005** | **23 font families. On `evaluation/recognition`'s 100-sample synthetic set: avg CER 4.66% (95.34% character accuracy), avg WER 15.58% — see Evaluation below** |

V5's numbers above are CER/WER on a different, purpose-built validation set than V1–V4's exact-match/edit-similarity numbers — the two are not directly comparable; see Evaluation for methodology.

## Evaluation

`evaluation/` holds everything needed to reproduce the numbers in this README:

```
evaluation/
├── recognition/     # 100 synthetic validation images (25/language) + comparison.csv + summary.csv
├── real_pdf/         # input/ (real PDFs) and outputs/ (every export format, per PDF)
└── scripts/          # run_benchmark.py, run_e2e_test.py, requirements.txt
```

**Recognition benchmark** — compares deployed V5 against stock `PP-OCRv6_medium_rec` on a 100-sample **synthetic** validation set (25 short phrases per language, rendered text — not real scans):

```bash
docker compose exec worker python evaluation/scripts/run_benchmark.py \
    --val-list evaluation/recognition/val_list.txt
```

| Language | V5 CER | V5 WER | Stock CER | Stock WER |
|---|---:|---:|---:|---:|
| en | 0.55% | 3.67% | 1.67% | 7.33% |
| gu | 4.72% | 17.67% | 92.00% | 100.00% |
| hi | 8.97% | 26.67% | 95.33% | 100.00% |
| mr | 4.42% | 14.33% | 94.01% | 100.00% |

Full per-sample results: [`evaluation/recognition/comparison.csv`](./evaluation/recognition/comparison.csv) / [`evaluation/recognition/summary.csv`](./evaluation/recognition/summary.csv).

Sample predictions (5 rows per language):

| Language | Actual | V5 Prediction | CER |
|---|---|---|---:|
| en | Invoice Number 48213 | Invoice Number 48213 | 0.00% |
| en | Total Amount Due 1024.50 | Total Amount Due 1024.50 | 0.00% |
| en | Payment Terms Net 30 Days | Payment Terms Net 30 Days | 0.00% |
| en | Shipping Address London | Shipping Addess London | 4.35% |
| en | Customer Name John Smith | Customer Name John Smith | 0.00% |
| hi | नमस्ते भारत | नमस्ते भारत | 0.00% |
| hi | कुल राशि चालान | कुल राश चिाान | 21.43% |
| hi | धन्यवाद आभार | धन्यवाद आभार | 0.00% |
| hi | संख्या और तारीख | संख्या और तारीख | 0.00% |
| hi | ग्राहक का नाम राज | ग्राहक का नाम राज | 0.00% |
| mr | नमस्कार महाराष्ट्र | नमस्कार महाराष्ट्र | 0.00% |
| mr | एकूण रक्कम दिनांक | एकूण रक्कम दनिांक | 11.76% |
| mr | धन्यवाद आपले | धन्यवाद आपले | 0.00% |
| mr | पावती क्रमांक | पावती क्रमांक | 0.00% |
| mr | ग्राहकाचे नाव सुरेश | ग्राहकाचे नाव सुरेश | 0.00% |
| gu | નમસ્તે ગુજરાત | નમસ્તે ગુજાત | 7.69% |
| gu | કુલ રકમ તારીખ | કુલ રકમ તારીખ | 0.00% |
| gu | આભાર તમારો | આભાર તમમાારો | 20.00% |
| gu | ચલણ નંબર | ચલણ ન નબર | 25.00% |
| gu | ગ્રાહકનું નામ પટેલ | ગ્રાહકનું નામ પટેલ | 0.00% |

**End-to-end pipeline test** — uploads a real PDF to a running stack and checks every download format:

```bash
pip install -r evaluation/scripts/requirements.txt
pytest evaluation/scripts/run_e2e_test.py -v
```

**Real-PDF processing** — `evaluation/real_pdf/` contains two real PDFs run through the full pipeline (upload → OCR → correction → every export format); see the per-document results below.

| PDF | Pages | Blocks | Avg Confidence | Avg Quality Score | Figures Cropped |
|---|---:|---:|---:|---:|---:|
| `real_sample.pdf` | 3 | 39 | 95.32% | 89.53% | 1 |
| `real_sample1.pdf` | 3 | 68 | 91.88% | 79.18% | 2 |

Figure crops are stored in MinIO at `results/{job_id}/p{page}_b{block}.png` and copied into `evaluation/real_pdf/outputs/<name>/figure_*.png` — e.g. `real_sample.pdf`'s page-3 figure (`p3_b5.png`, 997KB) and `real_sample1.pdf`'s two figures (`p1_b27.png`, `p2_b26.png`).

### Correction outcome breakdown

The latest reprocessing run (with a proactive rate limiter and exact-429-delay handling — see "Gemini rate limiting" below) was timed end-to-end: `real_sample.pdf` took **~250s**, `real_sample1.pdf` took **~518s** (3 pages each, OCR + structure detection + correction attempt + all 6 exports). Per-block correction outcome:

| PDF | `api_error` | `skipped_high_confidence` | `ok` (corrected) | n/a (table/figure) |
|---|---:|---:|---:|---:|
| `real_sample.pdf` | 30 | 7 | 0 | 2 |
| `real_sample1.pdf` | 59 | 6 | 0 | 3 |

**0 new corrections — every attempt hit `api_error`.** The actual root cause, read directly from the API's error payload this time, is more precise than previously thought: this key is capped at **20 requests/day** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) for `gemini-3.8-flash`, not just a per-minute limit — and today's quota was already exhausted by earlier testing. No amount of client-side pacing fixes a daily cap; it needs either a paid-tier key, a different provider, or waiting for the daily reset.

While verifying this, the exact-429-delay fix caught a real bug in itself: the API's `retryDelay` for a daily-quota 429 is enormous (~13.4 hours, since that's roughly how long until the daily reset), and honoring it literally would have slept the entire worker (concurrency=1) for that whole time, blocking every other job. Fixed with `GEMINI_MAX_HONORED_RETRY_DELAY_SECONDS` (default 60s) — a delay beyond that is treated as unrecoverable within this job and falls back to raw text immediately instead of sleeping through it. Confirmed working: the fallback now happens in under a second instead of hanging.

Because no new corrections succeeded, the before/after examples below are still from the one earlier successful run (same two documents, same correction logic, before today's daily quota was exhausted) — 28 blocks were genuinely corrected that run. These documents are predominantly **Gujarati**, with one English scanner watermark line; every number, date code, and identifier is unchanged:

| Language | Original (raw OCR) | Corrected | What changed |
|---|---|---|---|
| gu | વર૨સે છે વાયરોને ધોધમા૨ ચોમાસું - ખાલીખમ. સોરવરમાં વા વળો | વરસે છે વાયરોને ધોધમાર ચોમાસું - ખાલીખમ સરોવરમાં વા વળો | Stray digit "૨" removed from two words; spelling fix |
| gu | ંધારી સાંજનો. વાવી ઉજગરો બાર-બાર બાઉ મેં બાંધ્યો ઉ ઉાળો ! | અંધારી સાંજનો વાવી ઉજાગરો બાર-બાર ગાઉ મેં બાંધ્યો છે માળો ! | Missing leading letter restored; OCR-garble fixes |
| gu | ક્રમાlક: | ક્રમાંક: | Latin "l" misread for Gujarati matra, fixed |
| gu | વિદ્યાર્થાીએ અમારી સેસ્થામાં ચઆાલ સેમેસ્ટર / /7 અથવા વર્ષ 1/2//4 માં રૂ | વિદ્યાર્થીએ અમારી સંસ્થામાં ચાલુ સેમેસ્ટર / /7 અથવા વર્ષ 1/2//4 માં રૂ | Spelling only — `/ /7` and `1/2//4` (semester/year codes) byte-for-byte unchanged |
| gu | ંપ્રથમ વર્ષે અરજી કા વર્ષ ૨૬૨૭માં પ્વેશ પેવવેલ વિર્યા્થી માટે | (પ્રથમ વર્ષે અરજી કરતા વર્ષ ૨૬૨૭માં પ્રવેશ મેળવેલ વિદ્યાર્થી માટે) | Spelling/grammar only — year "૨૬૨૭" byte-for-byte unchanged |
| gu | મખ્યમંત્રી યહવા સ્વાવંબન યોજના હેઠળ સહાય મેળવવા માટે | મુખ્યમંત્રી યુવા સ્વાવલંબન યોજના હેઠળ સહાય મેળવવા માટે | Scheme-name spelling fixes only |
| gu | એનરોલમેન્ટ નંન્બર | એનરોલમેન્ટ નંબર | Doubled-letter typo fixed |
| gu | એનઆરઆઈ બેઠક પર પવેશ મેળવેલ નથી | એનઆરઆઈ બેઠક પર પ્રવેશ મેળવેલ નથી | Missing conjunct restored ("પવેશ"→"પ્રવેશ") |
| gu | સંસ્થાના વડાનં નામ, સહી અને સિક્કો | સંસ્થાના વડાનું નામ, સહી અને સિક્કો | Spelling fix only |
| en | Scanned with OEN Scanner | Scanned with OKEN Scanner | Scanner watermark OCR fix |

Full per-document outputs (all 6 formats + figure crops): [`evaluation/real_pdf/outputs/`](./evaluation/real_pdf/outputs/).

## Privacy & Cost

Post-OCR correction is **optional and off by default** (`CORRECTION_PROVIDER=none`). When a provider (Anthropic, OpenAI, or Gemini) is configured, page text is sent to that provider's API for correction, batched one call per page. Both `original_text` (raw OCR) and `corrected_text` are always stored, so a correction can be audited or reverted.

For Gemini specifically, `CORRECTION_RPM` (default 5) throttles calls to stay under its per-minute quota, and `GEMINI_MAX_HONORED_RETRY_DELAY_SECONDS` (default 60s) bounds how long a single 429 wait can be — see "Gemini rate limiting" in ARCHITECTURE.md §9.5.

**Measured token usage (n=2 pages)**: the only two successful Gemini calls obtained so far — 871+1,538 = 2,409 input tokens and 474+1,089 = 1,563 output tokens, averaging **~1,205 input / ~782 output tokens per page**. Two later reprocessing rounds intended to grow this sample instead hit the free-tier rate limit on every attempt — first the per-minute limit, then (after fixing that) the **daily** limit (20 requests/day for `gemini-3.8-flash`) — and produced zero additional successful calls. So this estimate is still from a 2-page sample, not a larger one; growing it needs a paid-tier key, a different provider, or waiting for the daily quota to reset. At Gemini Flash-tier list pricing (~$0.10/M input, ~$0.40/M output — confirm the current rate at [ai.google.dev/pricing](https://ai.google.dev/pricing), since this varies by model and changes over time), that's roughly:

```
(1205 / 1,000,000 × $0.10) + (782 / 1,000,000 × $0.40) ≈ $0.00043 / page
≈ $0.43 per 1,000 pages
```

This is a rough order-of-magnitude figure from a two-sample measurement, not a billed-cost guarantee — actual cost scales with page text density and varies by provider/model choice.

## License

See [`LICENSE`](./LICENSE).
