# DocScribe — Multilingual OCR Pipeline Skeleton

## Purpose
This repository contains the complete enterprise-grade skeleton for DocScribe, a high-throughput multilingual OCR system. It defines the structure for a FastAPI backend, a Celery task queue worker, Redis broker, PostgreSQL storage, and a React + Vite + TypeScript + Tailwind CSS v4 frontend.

## Future TODOs
- Implement deskew/denoise preprocessing using OpenCV.
- Integrate PaddleOCR and Surya OCR engines.
- Write routing rules based on confidence threshold scoring and multi-script checks.
- Build the final production Kubernetes Helm charts.

## Project Structure
```
ocr-pipeline/
├── ARCHITECTURE.md              # System architecture reference document
├── README.md                    # This guide
├── .env.example                 # Template for environment variables
├── docker-compose.yml           # Multi-service setup for local development
├── backend/                     # API and worker implementations
│   ├── app/                     # FastAPI application logic
│   ├── worker/                  # Celery worker, ML engine triggers, and pipelines
│   ├── requirements-common.txt  # Shared deps (API + worker)
│   ├── requirements-api.txt     # API-only deps (fastapi/uvicorn) — no ML stack
│   └── requirements-worker.txt  # Worker-only deps (paddleocr/surya/transformers/opencv)
├── frontend/                    # TypeScript + React + Tailwind v4 UI
│   ├── src/                     # Source modules (components, store, API clients)
│   └── package.json             # Frontend Node.js dependencies
└── infra/                       # Infrastructure configuration files (PostgreSQL / MinIO init)
```

## Getting Started

### Backend & Worker
1. Ensure Python 3.10+ is installed.
2. Create a virtual environment and install dependencies:
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate  # On Windows use venv\Scripts\activate
   pip install -r requirements-api.txt     # for running the FastAPI server
   # or: pip install -r requirements-worker.txt   # for running the Celery worker
   ```
3. Run the FastAPI development server:
   ```bash
   python -m uvicorn app.main:app --reload --port 8000
   ```
4. Start Celery worker:
   ```bash
   celery -A worker.celery_app worker --loglevel=info
   ```

### Frontend
1. Ensure Node.js 18+ is installed.
2. Install npm dependencies and run the dev server:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

### Multi-Service Docker Compose
You can run all components (API, Worker, Redis, MinIO, PostgreSQL, and Frontend) simultaneously with:
```bash
docker compose up --build
```
