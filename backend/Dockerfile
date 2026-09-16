# syntax=docker/dockerfile:1.7
# Purpose: Build stage for DocScribe backend FastAPI & Celery worker services.
# Both services share this Dockerfile/build context (they share the same
# app/ + worker/ source tree — worker imports app.core.config/db/models,
# and backend imports worker.celery_app), but install DIFFERENT dependency
# sets via REQUIREMENTS_FILE: the API never imports worker.tasks (Celery's
# include=["worker.tasks"] is only evaluated inside worker_main(), not on
# send_task()), so it doesn't need paddlepaddle/paddleocr/surya-ocr/
# transformers/opencv — see requirements-api.txt vs requirements-worker.txt.

# Future TODOs: Separate builder image from runner image (multi-stage) and include CUDA runtime dependencies.

FROM python:3.11-slim

ARG REQUIREMENTS_FILE=requirements-worker.txt

WORKDIR /app

# Install system dependencies needed for OpenCV, PyMuPDF, and psycopg2
# (shared across both images for simplicity — these apt packages are small
# relative to the pip-side torch/paddlepaddle savings the split targets)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-common.txt requirements-api.txt requirements-worker.txt ./

# Cache mount persists downloaded wheels across builds (and across a build
# getting killed/retried) — the worker dependency set includes torch/
# paddlepaddle (100s of MB each), so re-downloading from scratch every retry
# is costly on a slow connection. --no-cache-dir is dropped since it would
# defeat the mount by telling pip not to write to its cache in the first place.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r ${REQUIREMENTS_FILE}

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]