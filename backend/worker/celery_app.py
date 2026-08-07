# Purpose: Initialize the Celery application, configure Redis broker/backend paths, and register task definitions.
# Future TODOs: Configure task execution timeouts, dead letter queues, and custom serializer formats.

import os
from celery import Celery
from app.core.config import settings

# Lazy construction of Redis connection strings
REDIS_BROKER_URL = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"

celery_app = Celery(
    "docscribe_worker",
    broker=REDIS_BROKER_URL,
    backend=REDIS_BROKER_URL,
    include=["worker.tasks"]
)

# Optional configuration settings optimization
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True
)

if __name__ == "__main__":
    celery_app.start()
