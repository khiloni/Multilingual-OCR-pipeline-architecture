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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Instantiate settings lazily or globally
settings = Settings()
