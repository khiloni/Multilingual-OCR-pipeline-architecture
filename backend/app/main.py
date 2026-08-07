# Purpose: FastAPI application entrypoint, CORS configuration, API route setup, and startup health verification checks.
# Future TODOs: Configure global exception middleware, request telemetry logging, and Prometheus endpoints.

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.db import check_db_health
from app.api.routes import jobs, documents, webhooks

app = FastAPI(
    title="DocScribe OCR Pipeline API",
    description="Enterprise-grade multilingual OCR system with layout-aware fallback engine routing.",
    version="1.0.0",
)

# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production requirements
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")

@app.get("/health")
def health_check():
    """
    Health check endpoint returning system status. Runs lazy database check without crashing.
    """
    db_healthy = check_db_health()
    return {
        "status": "healthy" if db_healthy else "degraded",
        "services": {
            "api": "online",
            "database": "online" if db_healthy else "offline (lazy-fallback-active)"
        }
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
        reload=settings.DEBUG
    )
