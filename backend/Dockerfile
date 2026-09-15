# syntax=docker/dockerfile:1.7
# Purpose: Build stage for DocScribe backend FastAPI & Celery worker services.

# Future TODOs: Separate builder image from runner image (multi-stage) and include CUDA runtime dependencies.

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed for OpenCV, PyMuPDF, and psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Cache mount persists downloaded wheels across builds (and across a build
# getting killed/retried) — this dependency set includes torch/paddlepaddle
# (100s of MB each), so re-downloading from scratch every retry is costly on
# a slow connection. --no-cache-dir is dropped since it would defeat the
# mount by telling pip not to write to its cache in the first place.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]