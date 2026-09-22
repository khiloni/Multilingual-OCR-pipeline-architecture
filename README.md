# DocScribe

**DocScribe** is an open-source, multilingual OCR pipeline for extracting clean, structured text from multi-page PDF documents. It combines a fast primary OCR engine with a layout-aware fallback, producing structured JSON (with derived Markdown) instead of a flat text blob — text, layout, confidence, and language for every block, plus detected tables and figures.

---

## Table of Contents

- [Purpose](#purpose)
- [Features](#features)
- [Architecture](#architecture)
- [Fine-Tuned OCR Model](#fine-tuned-ocr-model)
- [Pipeline](#pipeline)
- [API](#api)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Current Status](#current-status)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [License](#license)

---

## Purpose

Documents need their textual content extracted reliably so downstream systems — search indexing, data entry automation, compliance checks, analytics — can consume it. Multilingual and structurally complex documents (mixed scripts, tables, figures) routinely defeat naive OCR. DocScribe is built to handle that case directly: language-aware extraction, structure-preserving output, and no dependency on paid cloud OCR APIs.

## Features

- **Multilingual OCR** — English, Hindi, Marathi, and Gujarati, with a fine-tuned recognition model (see below).
- **Hybrid engine routing** — a fast primary engine for the common case, with an automatic layout-aware fallback for low-confidence pages and scripts outside the primary model's language set.
- **Structured output** — every page returns text blocks with bounding boxes, per-block confidence, and language, not a text blob.
- **Reading-order correction** — multi-column pages are reconstructed in correct reading order, not top-to-bottom-then-interleaved.
- **Table extraction** — tables are returned as structured row/column/cell data (with merged-cell spans and per-cell confidence), not flattened text.
- **Figure & chart detection** — figures and charts are cropped, stored, and linked in the output, with captions matched where detectable.
- **Offline spell correction** — low-confidence tokens are corrected against per-language dictionaries (English, Hindi, Marathi, Gujarati), with every correction logged for auditability. Numeric values and ID-like tokens are never touched.
- **JSON + Markdown export** — JSON is the canonical output; Markdown is always derived from it, so the two never drift apart.
- **Async processing** — documents are queued and processed in the background, with live status polling from the UI.
- **Document management UI** — upload, track status, download results, and delete documents from a single dashboard.

## Architecture

DocScribe runs as a set of Docker Compose services: a React frontend, a FastAPI backend, a Celery worker pool (backed by Redis) that runs the OCR pipeline, PostgreSQL for metadata, and MinIO for file storage.

Full system design — diagrams, data flow, database schema, and the API contract — lives in [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Fine-Tuned OCR Model

The recognition stage uses a fine-tuned PaddleOCR model trained specifically for this pipeline's target languages:

- **Languages:** English, Hindi, Marathi, Gujarati
- **Training data:** 40,000 synthetic images (10,000 per language)
- **Font coverage:** 86 fonts across 23 font families
- **Status:** the final inference-ready model is integrated into the pipeline at `model/paddle/inference/rec_finetuned/`

## Pipeline

1. **Upload** — a PDF is submitted via the API and stored in object storage; a job is queued.
2. **Rasterize** — each page is rendered to an image.
3. **Preprocess** — deskew and denoise.
4. **Recognize** — the primary OCR engine runs on every page; pages that come back low-confidence, or contain a script the primary model wasn't trained on, are re-run through the fallback engine and merged.
5. **Detect structure** — tables and figures/charts are located, extracted, and (for figures) cropped to object storage.
6. **Normalize** — all blocks are reordered into correct reading order and assembled into the common output schema.
7. **Spell-correct** — low-confidence text tokens are corrected against offline dictionaries; every correction is logged.
8. **Persist & serve** — structured JSON and derived Markdown are stored and made available via the API and UI.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/jobs` | Upload a PDF and start processing |
| `GET` | `/api/v1/jobs/{job_id}` | Poll job status |
| `GET` | `/api/v1/jobs/{job_id}/result?format=json\|markdown` | Fetch structured output |
| `GET` | `/api/v1/jobs/{job_id}/pages/{n}` | Fetch a single page's result and preview |
| `POST` | `/api/v1/jobs/{job_id}/reprocess` | Re-run OCR for a document |
| `GET` | `/api/v1/documents` | List uploaded documents |
| `DELETE` | `/api/v1/documents/{id}` | Remove a document and its results |

Full contract in [`ARCHITECTURE.md`](./ARCHITECTURE.md#11-rest-api-contract).

## Getting Started

### Docker Compose (recommended)

Runs every service — frontend, backend, worker, Redis, PostgreSQL, and MinIO — together:

```bash
docker compose up --build
```

- Frontend: [http://localhost:5173](http://localhost:5173)
- Backend API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- MinIO console: [http://localhost:9001](http://localhost:9001)

### Running services individually

**Backend API**
```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-api.txt
python -m uvicorn app.main:app --reload --port 8000
```

**Worker** (needs the ML dependencies)
```bash
cd backend
pip install -r requirements-worker.txt
celery -A worker.celery_app worker --loglevel=info
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

Copy `.env.example` to `.env` and adjust as needed for local (non-Docker) runs.

## Project Structure

```
ocr-pipeline/
├── ARCHITECTURE.md          # System design, diagrams, schemas, API contract
├── docker-compose.yml
├── frontend/                # React + Vite + TypeScript + Tailwind
├── backend/
│   ├── app/                 # FastAPI application (routes, models, schemas)
│   └── worker/               # Celery worker and OCR pipeline
│       └── pipeline/
│           ├── engines/      # PaddleOCR, Surya, table/figure detection
│           ├── preprocess.py
│           ├── router.py     # engine selection logic
│           ├── normalize.py  # common schema output
│           └── spellcheck.py
├── model/paddle/inference/rec_finetuned/   # fine-tuned recognition model
└── infra/                   # PostgreSQL init
```

## Current Status

The core pipeline is complete and functional end to end: upload, queued processing, hybrid engine routing, reading-order correction, table and figure/chart detection, offline spell correction, and a document management UI with JSON/Markdown export.

## Limitations

- Language support is currently English, Hindi, Marathi, and Gujarati.
- Spell-check dictionaries are sourced from general-purpose corpora, so domain-specific vocabulary (legal, medical, invoice-specific terms) is not recognized.
- Chart regions are detected and flagged for review, but underlying data values are not extracted from chart images — that's a separate problem, and DocScribe doesn't guess.
- Table and figure detection runs on every page rather than being confidence-gated, since structure detection isn't a "low confidence" problem — this is a deliberate tradeoff, not a bug.

## Roadmap

- Additional language support (Tamil, Telugu, Bengali, Kannada)
- Multi-script pages beyond the current language set (CJK, Arabic/RTL)
- Downstream integration: public REST API stability, webhooks on job completion
- Metrics dashboard and human-in-the-loop correction UI

## License

See [`LICENSE`](./LICENSE).
