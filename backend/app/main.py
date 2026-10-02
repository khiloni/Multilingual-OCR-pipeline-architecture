# Purpose: FastAPI application entrypoint, CORS configuration, API route setup, and startup health verification checks.

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import documents, jobs, search, webhooks
from app.core.config import settings
from app.core.db import check_db_health
from app.services.storage import storage_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: verify Postgres + MinIO (ensure bucket). Do not crash on degraded deps."""
    db_ok = check_db_health()
    storage_ok = storage_service.check_storage_health()
    logger.info(
        "Startup health checks — database=%s storage=%s",
        "ok" if db_ok else "degraded",
        "ok" if storage_ok else "degraded",
    )
    yield


app = FastAPI(
    title="DocScribe OCR Pipeline API",
    description="Enterprise-grade multilingual OCR system with layout-aware fallback engine routing.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")


@app.get("/health")
def health_check():
    """
    Health check endpoint returning system status. Runs lazy connectivity checks without crashing.
    """
    db_healthy = check_db_health()
    storage_healthy = storage_service.check_storage_health()
    overall = "healthy" if (db_healthy and storage_healthy) else "degraded"
    return {
        "status": overall,
        "services": {
            "api": "online",
            "database": "online" if db_healthy else "offline (lazy-fallback-active)",
            "storage": "online" if storage_healthy else "offline (lazy-fallback-active)",
        },
    }


@app.get("/")
def root():
    return {
        "message": "Welcome to DocScribe Multilingual OCR Pipeline. API documentation is at /docs."
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
