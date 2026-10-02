# Purpose: Central configuration using pydantic-settings to manage DB, Redis, MinIO endpoints and thresholds.
# Future TODOs: Add secret rotation capabilities, AWS Secrets Manager integration, and OAuth2 variables.

import os
from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Settings model for loading configurations from environment variables or .env file.
    """
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    DEBUG: bool = True

    # Database
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres_password"
    POSTGRES_DB: str = "docscribe"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # MinIO / Object Storage
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET_NAME: str = "docscribe-storage"
    MINIO_SECURE: bool = False

    # OCR Configuration
    PADDLE_CONFIDENCE_THRESHOLD: float = 0.85
    SURYA_CONFIDENCE_THRESHOLD: float = 0.75
    ENABLE_SURYA_FALLBACK: bool = True

    # Image-variant retry (Phase 2 item 2) — before falling back to Surya,
    # retry the SAME engine on contrast-enhanced / adaptive-threshold
    # variants of a low-confidence page and keep whichever scores best.
    ENABLE_IMAGE_VARIANT_RETRY: bool = True
    IMAGE_VARIANT_MAX_ATTEMPTS: int = 2  # caps latency: at most 2 variants tried

    # API-based correction (Phase 2 item 0b) — replaces the offline
    # SymSpell path. "none" disables correction entirely (raw OCR text
    # passes through); "legacy_symspell" keeps the old offline path behind
    # this same flag for comparison/fallback.
    # Defaults OFF — correction sends page text to an external API when
    # enabled, so a fresh deployment must opt in explicitly rather than
    # start sending document content off-host by default.
    CORRECTION_PROVIDER: str = "none"  # anthropic | openai | gemini | legacy_symspell | none
    CORRECTION_API_KEY: str = ""
    CORRECTION_MODEL: str = "claude-opus-5"
    CORRECTION_TIMEOUT_SECONDS: float = 30.0
    CORRECTION_MAX_RETRIES: int = 2
    # Blocks at/above this confidence are left alone — not worth the API
    # call, and reduces cost/risk on text that's already almost certainly right.
    CORRECTION_SKIP_CONFIDENCE: float = 0.98
    # A "correction" that changes more than this fraction of the text
    # (normalized edit distance) is treated as a rewrite, not a spell fix,
    # and rejected — the raw OCR text is kept instead.
    CORRECTION_MAX_EDIT_DISTANCE_RATIO: float = 0.30

    # OCR quality score (Phase 2 item 5) — weights must sum to 1.0.
    QUALITY_WEIGHT_CONFIDENCE: float = 0.35
    QUALITY_WEIGHT_TEXT_QUALITY: float = 0.25
    QUALITY_WEIGHT_CHARACTER_QUALITY: float = 0.15
    QUALITY_WEIGHT_PAGE_COVERAGE: float = 0.15
    QUALITY_WEIGHT_LANGUAGE_CONSISTENCY: float = 0.10
    # Below this, router.py treats a page as low-quality even if raw
    # confidence alone cleared PADDLE_CONFIDENCE_THRESHOLD — gates the
    # image-variant retry and Surya fallback alongside confidence.
    QUALITY_SCORE_THRESHOLD: float = 0.70

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Instantiate settings lazily or globally
settings = Settings()
