# Purpose: API route endpoints for managing downstream webhooks (dispatching notifications upon job completion).
# Future TODOs: Implement signed payloads (HMAC signature validation) and exponential backoff retry scheduling.

import uuid
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
from app.core.db import get_db

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

class WebhookRegisterSchema(BaseModel):
    url: HttpUrl
    secret_token: str

@router.post("", status_code=status.HTTP_201_CREATED)
def register_webhook(
    payload: WebhookRegisterSchema,
    db: Session = Depends(get_db)
):
    """
    Registers a target URL to receive status posts when an OCR task completes.
    """
    # TODO: Persist webhook endpoint config in db.
    return {"message": "Webhook URL registered successfully.", "webhook_id": uuid.uuid4()}
