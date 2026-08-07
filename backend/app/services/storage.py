# Purpose: S3/MinIO service abstraction for uploading/downloading documents and structured extraction JSONs.
# Future TODOs: Integrate with local minio/S3 SDK, handle chunked file uploads, and handle pre-signed URL generations.

import logging
from typing import BinaryIO
from uuid import UUID

logger = logging.getLogger(__name__)

class StorageService:
    """
    Service client dealing with MinIO object storage read/write operations.
    """
    def __init__(self) -> None:
        # TODO: Initialize boto3.client("s3") using app.core.config settings.
        logger.info("Initializing Object Storage service connection pool (lazy connection)")

    def upload_document(self, file_id: UUID, file_obj: BinaryIO, filename: str) -> str:
        """
        Uploads a raw PDF document file to MinIO bucket.
        Returns the unique path or key of the stored object.
        """
        # TODO: Implement actual boto3 upload logic.
        logger.info(f"Mock upload for document: {filename} with id: {file_id}")
        return f"documents/{file_id}/{filename}"

    def download_document(self, storage_path: str) -> bytes:
        """
        Retrieves the raw PDF binary contents from the storage.
        """
        # TODO: Implement actual boto3 download logic.
        logger.info(f"Mock download for storage path: {storage_path}")
        return b"%PDF-1.4 mock pdf data"

    def upload_result(self, job_id: UUID, content: str, file_format: str = "json") -> str:
        """
        Saves the structured OCR output (JSON or Markdown) in object storage.
        """
        # TODO: Implement result saving.
        logger.info(f"Mock uploading result for job: {job_id} as format: {file_format}")
        return f"results/{job_id}/result.{file_format}"

    def delete_document(self, storage_path: str) -> bool:
        """
        Deletes the document from the storage server.
        """
        # TODO: Implement actual boto3 delete logic.
        logger.info(f"Mock deleting storage path: {storage_path}")
        return True

# Initialize a default global instance of the storage service
storage_service = StorageService()
