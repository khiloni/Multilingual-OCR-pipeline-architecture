# Multilingual OCR Pipeline — System Architecture

**Project codename:** DocScribe (placeholder — rename as needed)
**Status:** Planning / Base Reference Document
**Last updated:** 2026-09-13

> This file is the single source of truth for the application's design. Every module, decision, and diagram lives here first before code is written. Update this file whenever architecture decisions change.

---

## 1. Problem Statement

Documents need their textual content extracted reliably so downstream systems (search indexing, data entry automation, compliance checks, analytics) can consume it. Without a robust, language-aware OCR pipeline, multilingual and structurally complex documents fail to process or produce low-quality, unreliable text.

## 2. Expected Solution

An end-to-end, open-source OCR pipeline that:
- Accepts multi-page PDF documents as input
- Produces clean, structured, language-correct extracted text as output
- Is suitable for direct downstream consumption without manual correction

## 3. Design Principles

1. **Open-source first** — no dependency on paid cloud OCR APIs for the core pipeline.
2. **Hybrid engine strategy** — fast traditional OCR for the common case, VLM/layout-aware fallback for hard pages (tables, skew, mixed scripts, low confidence).
3. **Structured output over raw text** — every extraction returns text + layout + confidence + language, not a text blob.
4. **CPU-first, GPU-optional** — the app must run end-to-end without a GPU; GPU unlocks the higher-quality fallback path.
5. **Downstream-agnostic** — output schema is generic JSON/Markdown so any consumer (search index, RPA bot, compliance scanner) can use it without custom parsing.

---

## 4. Feature Roadmap

Full checklist-level tracking lives in [`FEATURE_ROADMAP.md`](./FEATURE_ROADMAP.md) — keep that file updated as items ship. Summary:

| Phase | Features | Status |
|---|---|---|
| **Phase 0 — MVP** | PDF upload, page-level text extraction, language detection, basic cleanup (dehyphenation, whitespace/encoding fixes), download as .txt/.json | In Progress |
| **Phase 1 — Quality & Structure** | Layout-aware extraction (reading order, headings, tables), per-block confidence scoring, Markdown + JSON structured output, async batch processing | Planned |
| **Phase 2 — Multilingual & Robustness** | Multi-script support (Latin, Devanagari, CJK, Arabic/RTL), mixed-language-per-page handling, deskew/denoise preprocessing, traditional→VLM fallback routing | Planned |
| **Phase 3 — Downstream Integration** | REST API for external consumers, webhooks on job completion, PII/compliance field flagging, audit trail | Planned |
| **Phase 4 — Ops & Scale** | Metrics dashboard, human-in-the-loop correction UI, engine versioning/A-B, auth + multi-tenant, rate limiting | Backlog |

---

## 5. OCR Engine Comparison (Cost / Complexity / Accuracy)

Basis for choosing PaddleOCR as primary and Surya as fallback in the routing logic below.

| Engine | Type | License | Params / Footprint | Speed (approx.) | Multilingual | Table/Layout Support | Setup Complexity | Hardware | Best Fit In This Pipeline |
|---|---|---|---|---|---|---|---|---|---|
| **Tesseract** | Traditional CV + LSTM | Apache 2.0 | Lightweight, no GPU needed | ~25 pages/min (CPU) | 100+ languages, weaker on script mixing | Poor — no reliable table/form structure | Low — simple install, minimal deps | CPU only | Cheap baseline / offline fallback, not primary engine |
| **PaddleOCR (PP-OCRv6 + PP-Structure)** | Traditional CV pipeline | Apache 2.0 | Small-to-mid models | ~120 pages/min (GPU, RTX 3090-class); slower on CPU | 80+ languages incl. strong CJK | Good — PP-Structure gives real table extraction | Medium — PaddlePaddle dependency setup is more involved than Tesseract | CPU-viable, GPU recommended for throughput | **Primary engine** — best balance of speed, accuracy, and structure for the bulk of pages |
| **Surya OCR** | VLM-based, layout-native | GPL-3.0 | ~650M params | Slower than Paddle, faster than larger VLMs | Strong, layout-aware across scripts | Strong — layout detection + OCR in one pass | Medium-High — needs PyTorch + model weights, GPU strongly preferred | GPU recommended | **Fallback engine** — routed to for low-confidence, complex-layout, or mixed-script pages. Pinned to `surya-ocr==0.17.1`: releases ≥0.20.0 replaced the plain-PyTorch API with a `SuryaInferenceManager` that mandates a separate vllm (NVIDIA GPU) or llama.cpp inference server — too heavy for this CPU-first Docker Compose setup, so we stay on the last pre-overhaul release. |
| **Dots.OCR / DeepSeek-OCR (VLM class)** | Large VLM, layout-native | Open weights (model-specific) | Multi-billion params | Slow, GPU-bound | Very strong, especially mixed/rare scripts | Excellent — native Markdown/JSON structured output | High — `trust_remote_code`, larger GPU memory footprint | GPU required | Reserved for Phase 2+ hardest cases (rare scripts, badly degraded scans) — optional, not in MVP |

**Why this shapes the routing logic (Section 9):** Tesseract is excluded from the live pipeline (kept only as an emergency CPU-only fallback if PaddleOCR isn't available); PaddleOCR handles the majority of pages cheaply; Surya is invoked selectively rather than by default, since it costs more compute per page. The heavier VLMs (Dots.OCR/DeepSeek-OCR) are deferred until there's a clear case volume that justifies their GPU cost.

---

## 6. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + Vite + Tailwind CSS | Fast dev loop, consistent with existing stack, good for upload/preview UI |
| Backend API | FastAPI (Python) | Async-native, same language as the ML pipeline (no cross-language glue) |
| Job Queue | Celery + Redis | Multi-page PDFs are slow — must not block the request thread |
| Primary OCR engine | PaddleOCR (PP-OCRv6 / PP-Structure) | Fast, CPU-viable, strong multilingual + table support |
| Fallback OCR engine | Surya OCR (or Dots.OCR-class VLM) | Layout-aware, used for low-confidence / complex pages |
| PDF → image | PyMuPDF (fitz) / pdf2image | Reliable page rasterization |
| Preprocessing | OpenCV | Deskew, denoise, binarization |
| Language ID | fastText langid / PaddleOCR built-in | Per-block language tagging |
| Relational DB | PostgreSQL | Job metadata, document records, audit trail |
| Object storage | S3-compatible (MinIO locally, S3 in prod) | Original PDFs + extracted outputs |
| Search (Phase 3) | OpenSearch / Elasticsearch | Downstream search-indexing consumer |
| Deployment | Docker Compose (dev) → container orchestration (prod, later) | Reproducible multi-service setup |

---

## 7. High-Level System Architecture

```mermaid
flowchart LR
    subgraph Client
        A[React + Vite Frontend]
    end

    subgraph API["FastAPI Backend"]
        B[Upload / Job API]
        C[Status / Results API]
    end

    subgraph Queue["Async Processing"]
        D[Redis Broker]
        E[Celery Worker Pool]
    end

    subgraph Pipeline["OCR Pipeline Core"]
        F[PDF to Image]
        G[Preprocess: Deskew/Denoise]
        H{Engine Router}
        I[PaddleOCR - Fast Path]
        J[Surya/VLM - Fallback Path]
        K[Structure Normalizer]
    end

    subgraph Storage
        L[(PostgreSQL - Metadata)]
        M[(Object Storage - Files)]
    end

    subgraph Downstream["Downstream Consumers"]
        N[Search Index]
        O[Data Entry Automation]
        P[Compliance Checks]
    end

    A -->|Upload PDF| B
    B --> M
    B --> D
    D --> E
    E --> F --> G --> H
    H -->|confidence OK| I
    H -->|low confidence / table / complex| J
    I --> K
    J --> K
    K -->|structured JSON/Markdown| L
    K --> M
    A -->|Poll/WebSocket| C
    C --> L
    L --> N
    L --> O
    L --> P
```

---

## 8. Data Flow (Sequence Diagram)

```mermaid
sequenceDiagram
    actor User
    participant FE as React Frontend
    participant API as FastAPI
    participant Q as Redis/Celery
    participant W as OCR Worker
    participant DB as PostgreSQL
    participant S3 as Object Storage

    User->>FE: Upload multi-page PDF
    FE->>API: POST /jobs (multipart PDF)
    API->>S3: Store raw PDF
    API->>DB: Create job record (status=queued)
    API->>Q: Enqueue OCR task (job_id)
    API-->>FE: 202 Accepted {job_id}

    Q->>W: Dispatch task
    W->>S3: Fetch PDF
    loop For each page
        W->>W: Rasterize page
        W->>W: Deskew / denoise
        W->>W: Run PaddleOCR
        alt confidence < threshold OR table/complex layout detected
            W->>W: Run Surya/VLM fallback
        end
        W->>W: Normalize to common schema
    end
    W->>DB: Save structured results + confidence + status=done
    W->>S3: Store output JSON/Markdown

    FE->>API: GET /jobs/{job_id}
    API->>DB: Fetch status/results
    API-->>FE: 200 {status, results_url}
    FE-->>User: Show extracted text + confidence + download links
```

---

## 9. Engine Routing Logic

```mermaid
flowchart TD
    Start([Page Image Ready]) --> Detect[Detect layout type:\ntext / table / mixed script / scanned-noisy]
    Detect --> Run[Run PaddleOCR]
    Run --> Score{Confidence >= threshold\nAND no table\nAND single script?}
    Score -->|Yes| Accept[Accept PaddleOCR output]
    Score -->|No| Fallback[Run Surya: detection + recognition\nlayout-aware, not just a recognition swap]
    Fallback --> Merge[Merge best-of-both by block IOU:\nSurya's OWN bboxes are kept for its regions,\nPaddle only wins individual overlapping blocks\nwith higher confidence]
    Accept --> Normalize[Normalize to common schema]
    Merge --> Normalize
    Normalize --> Spell[Automated spell correction:\nlow-confidence tokens only, en/hi/gu/mr]
    Spell --> Output([Structured Block:\ntext, bbox, lang, confidence, type,\nspell_corrections audit log])
```

Surya is a **structure-aware fallback**, not a drop-in recognition swap: its own `DetectionPredictor` produces the bboxes for the regions it covers, and `EngineRouter._merge_blocks()` (`backend/worker/pipeline/router.py`) keeps those boxes rather than pasting Surya's text into PaddleOCR's (possibly wrong) geometry. That distinction is what makes the fallback actually useful on multi-column pages, tables, and rotated/skewed scans, rather than a checkbox confidence threshold.

---

## 10. Common Output Schema

Every page/document, regardless of which engine produced it, is normalized into this shape before storage or delivery. **JSON is the canonical/authoritative format; Markdown is always a derived export**, never the source of truth — this matters most for tables and multilingual text, where Markdown alone would be lossy.

### 10.1 Multilingual text policy

- Text is stored **in its original language** per block — no auto-translation to English during extraction. Compliance and audit use cases require the source text to be preserved exactly as it appeared.
- Each block carries its own `language` (ISO 639-1) since a single page or document can mix scripts.
- Translation is **opt-in and additive**, never destructive: a block may later gain `translated_text` / `translated_lang` fields populated by a separate downstream step (Phase 3+), but the original `text` field is never overwritten.
- Downstream search indexing uses per-language analyzers (OpenSearch/Elasticsearch) keyed off the `language` field, rather than requiring translation to be searchable.

### 10.2 Table policy

- Tables are stored as **structured cell data** (rows/cols/cell text/spans/confidence), not as a flat text blob or a Markdown table at the source-of-truth layer — Markdown can't represent merged cells or per-cell confidence.
- Markdown table export is generated by walking the `table.cells` array; merged cells are flattened as best-effort for the Markdown view, with a note that JSON is authoritative for anything needing true structure.

### 10.3 Image / chart policy

- Layout-detected figure regions are **cropped from the rasterized page and stored as image files in object storage**; the JSON block holds a reference URL, not raw bytes.
- Plain images/diagrams: crop + store + attach nearby caption text if detected adjacent to the bbox.
- Charts/graphs: OCR does **not** attempt to extract underlying data values from a chart (that's chart understanding, a different problem, and guessing is worse than not guessing — especially for compliance use). Chart regions are flagged `subtype: "chart"` with `needs_review: true`.
- Optional Phase 2+: a VLM-generated `alt_text` description for search/accessibility, explicitly labeled as *generated*, never presented as extracted data.

### 10.4 Spell-correction policy

- After normalization, each text block passes through automated, offline spell correction (`backend/worker/pipeline/spellcheck.py`, SymSpell-based) — but **only** for tokens where OCR confidence is below 0.90, and only for languages with a loaded frequency dictionary (currently `en`, `hi` — see the known-gaps note below). Numerics and alphanumeric IDs are never touched, protecting invoice numbers, dates, and codes.
- Every correction is recorded on the block as `spell_corrections: [{original, corrected, edit_distance}]` — an empty list means nothing was changed, not that correction didn't run. This is the audit trail for the "we auto-corrected X" claim.
- **Known limitation:** the dictionary source (hermitdave/FrequencyWords) has no Gujarati or Marathi wordlists at all (confirmed 404, not a download-and-forgot gap) — `gu`/`mr` blocks pass through `correct_block_text()` as a safe no-op until a dictionary from another source (e.g. Leipzig Corpora Collection) is wired in. `en`/`hi` are live. The dictionaries themselves are subtitle-derived (colloquial), so domain vocabulary (invoice/legal/medical terms) won't be recognized even for the active languages — flag this to reviewers rather than presenting corrections as domain-validated.

```json
{
  "document_id": "uuid",
  "filename": "sample.pdf",
  "page_count": 12,
  "pages": [
    {
      "page_number": 1,
      "language_detected": ["en", "hi"],
      "blocks": [
        {
          "block_id": "p1_b1",
          "type": "heading | paragraph | table | list | caption | figure",
          "text": "Extracted text content",
          "bbox": [x0, y0, x1, y1],
          "confidence": 0.97,
          "language": "en",
          "engine_used": "paddleocr | surya",
          "translated_text": null,
          "translated_lang": null,
          "spell_corrections": []
        },
        {
          "block_id": "p1_b2",
          "type": "table",
          "bbox": [x0, y0, x1, y1],
          "confidence": 0.91,
          "table": {
            "rows": 4,
            "cols": 3,
            "cells": [
              {"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "Item", "is_header": true, "confidence": 0.95},
              {"row": 0, "col": 1, "row_span": 1, "col_span": 2, "text": "Details", "is_header": true, "confidence": 0.93}
            ]
          }
        },
        {
          "block_id": "p1_b3",
          "type": "figure",
          "subtype": "image | chart",
          "bbox": [x0, y0, x1, y1],
          "confidence": 0.88,
          "image_url": "minio://results/doc123/p1_b3.png",
          "caption": "Figure 2: Regional sales breakdown",
          "alt_text": null,
          "needs_review": true
        }
      ]
    }
  ],
  "metadata": {
    "processed_at": "2026-08-07T10:00:00Z",
    "avg_confidence": 0.94,
    "low_confidence_pages": [4, 9]
  }
}
```

Markdown output is derived from this same schema (headings → `#`, tables → best-effort Markdown tables, figures → `![caption](image_url)`, etc.) so both formats stay in sync with JSON as the source of truth.

---

## 11. Database Schema (ER Diagram)

```mermaid
erDiagram
    DOCUMENTS ||--o{ JOBS : has
    JOBS ||--o{ PAGES : produces
    PAGES ||--o{ BLOCKS : contains

    DOCUMENTS {
        uuid id PK
        string filename
        string storage_path
        int page_count
        timestamp uploaded_at
    }
    JOBS {
        uuid id PK
        uuid document_id FK
        string status
        float avg_confidence
        timestamp started_at
        timestamp completed_at
    }
    PAGES {
        uuid id PK
        uuid job_id FK
        int page_number
        string languages_detected
    }
    BLOCKS {
        uuid id PK
        uuid page_id FK
        string type
        string subtype
        text content
        json bbox
        float confidence
        string language
        string engine_used
        text translated_text
        string translated_lang
        json table_data
        string image_url
        string caption
        boolean needs_review
    }
```

> `type` distinguishes heading/paragraph/table/list/caption/figure. `table_data` (rows/cols/cells) is only populated for `type=table`; `image_url`/`caption`/`needs_review`/`subtype` only for `type=figure`. Sparse columns are acceptable here since block volume per page is small — a separate `TABLE_CELLS` child table is a reasonable normalization if table volume grows large (see Section 16 open decisions).

---

## 12. REST API Contract (draft)

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/jobs` | Upload PDF, create job, returns `job_id` |
| `GET` | `/api/v1/jobs/{job_id}` | Poll job status (`queued`, `processing`, `done`, `failed`) |
| `GET` | `/api/v1/jobs/{job_id}/result?format=json\|markdown` | Fetch structured output |
| `GET` | `/api/v1/jobs/{job_id}/pages/{n}` | Fetch single-page result (with preview image) |
| `POST` | `/api/v1/jobs/{job_id}/reprocess` | Re-run with different engine/settings |
| `GET` | `/api/v1/documents` | List uploaded documents (paginated) |
| `DELETE` | `/api/v1/documents/{id}` | Remove document + associated jobs |
| `POST` | `/api/v1/webhooks` *(Phase 3)* | Register a callback URL for job completion |

---

## 13. Deployment Diagram (Docker Compose — Dev/Demo)

```mermaid
flowchart TB
    subgraph Docker Compose Network
        FE[frontend\nReact/Vite : 5173]
        BE[backend\nFastAPI : 8000]
        WK[worker\nCelery]
        RD[(redis : 6379)]
        PG[(postgres : 5432)]
        MI[(minio : 9000)]
    end

    User((Browser)) --> FE
    FE --> BE
    BE --> RD
    RD --> WK
    WK --> PG
    WK --> MI
    BE --> PG
    BE --> MI
```

GPU note: if the Surya/VLM fallback is enabled, the `worker` service needs CUDA runtime access (or is split into a separate `gpu-worker` service pinned to a GPU-enabled host, with the CPU worker still handling the PaddleOCR fast path).

---

## 14. Repository / Folder Structure

```
ocr-pipeline/
├── ARCHITECTURE.md              # this file
├── docker-compose.yml
├── frontend/
│   ├── src/
│   │   ├── components/          # UploadForm, JobStatus, PagePreview, ResultViewer
│   │   ├── pages/
│   │   ├── api/                 # API client
│   │   └── store/                # state management
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI entrypoint
│   │   ├── api/routes/           # jobs.py, documents.py, webhooks.py
│   │   ├── models/               # SQLAlchemy models
│   │   ├── schemas/              # Pydantic schemas (matches Section 9)
│   │   ├── services/             # storage.py, queue.py
│   │   └── core/                 # config, db session
│   ├── worker/
│   │   ├── tasks.py               # Celery task definitions
│   │   ├── pipeline/
│   │   │   ├── preprocess.py      # deskew, denoise
│   │   │   ├── engines/
│   │   │   │   ├── paddle_engine.py
│   │   │   │   └── surya_engine.py
│   │   │   ├── router.py          # engine selection logic (Section 9)
│   │   │   └── normalize.py       # common schema output
│   │   └── celery_app.py
│   ├── tests/
│   └── requirements.txt
└── infra/
    ├── minio/
    └── postgres/
```

---

## 15. Non-Functional Requirements

- **Reliability:** failed pages must not fail the whole document job — partial results with per-page status.
- **Observability:** structured logs per job/page, average confidence tracked over time.
- **Idempotency:** re-uploading the same file (hash match) should reuse cached results by default.
- **Data retention:** raw PDFs and outputs retained per a configurable policy (relevant for compliance use cases).
- **Extensibility:** engine router must support adding new engines (e.g., Dots.OCR, DeepSeek-OCR) without touching the schema layer.

---

## 16. Open Decisions (to revisit)

- [ ] Target script set for Phase 2 (Indian regional scripts vs. broader multilingual)
- [ ] GPU availability for VLM fallback — local, Colab, or cloud
- [ ] Deployment target: hackathon demo only vs. long-term hosted service
- [ ] Human-in-the-loop correction UI — in scope for Phase 4 or dropped

---

## 17. Change Log

| Date | Change |
|---|---|
| 2026-08-07 | Initial architecture document created |
| 2026-08-07 | Added OCR engine cost/complexity/accuracy comparison table (Section 5) |
| 2026-08-30 | Locked multilingual (no auto-translation), table (structured JSON, Markdown derived), and image/chart (crop+store, no chart-value guessing) policies in Section 10; updated BLOCKS entity accordingly |
| 2026-08-30 | Extracted Section 4 detail into standalone `FEATURE_ROADMAP.md`; Section 4 now links to it |
| 2026-09-13 | Implemented real Surya fallback engine (detection + recognition, pinned to `surya-ocr==0.17.1` — see §5/§9 for why), wired automated spell correction into the normalize stage (§10.4), fixed the `/jobs/{id}/result`, `/jobs/{id}/pages/{n}`, `/jobs/{id}/reprocess`, `GET /documents`, `DELETE /documents/{id}` stubs to read/write real DB + MinIO state, added page-preview image upload. Fine-tuned PP-OCRv6 recognition model wiring still blocked on exporting `model/paddle/best_model/` (raw training checkpoint, not a PaddleOCR-loadable inference export) and locating the training `dict.txt`. |
