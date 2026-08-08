# Purpose: Build stage for DocScribe backend FastAPI & Celery worker services.

# Future TODOs: Separate builder image from runner image (multi-stage) and include CUDA runtime dependencies.

FROM python:3.10-slim

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

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]