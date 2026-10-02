# Purpose: S3/MinIO service abstraction for uploading/downloading documents and structured extraction JSONs.
# Uses boto3 against the MinIO S3-compatible API (local) or real S3 (prod).

import json
import logging
from io import BytesIO
from typing import BinaryIO, Union
from uuid import UUID

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """
    Service client dealing with MinIO object storage read/write operations.
    """

    def __init__(self) -> None:
        self._client = None
        self.bucket = settings.MINIO_BUCKET_NAME

    @property
    def client(self):
        """Lazy boto3 S3 client pointed at MinIO (or S3)."""
        if self._client is None:
            scheme = "https" if settings.MINIO_SECURE else "http"
            endpoint_url = f"{scheme}://{settings.MINIO_ENDPOINT}"
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=settings.MINIO_ACCESS_KEY,
                aws_secret_access_key=settings.MINIO_SECRET_KEY,
                region_name="us-east-1",
                config=Config(signature_version="s3v4"),
            )
            logger.info("Initialized MinIO/S3 client endpoint=%s bucket=%s", endpoint_url, self.bucket)
        return self._client

    def ensure_bucket(self) -> None:
        """Create the configured bucket if it does not already exist."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)
            logger.info("Created object storage bucket: %s", self.bucket)
        self._ensure_results_public_read()

    def _ensure_results_public_read(self) -> None:
        """
        Grants anonymous GetObject on the results/* prefix only (page preview
        PNGs, result JSON/Markdown) — never on documents/* (raw uploaded PDFs
        stay private). The frontend renders page preview images directly from
        MinIO by URL, which requires this; best-effort since not every
        S3-compatible backend supports put_bucket_policy the same way.
        """
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{self.bucket}/results/*"],
                }
            ],
        }
        try:
            self.client.put_bucket_policy(Bucket=self.bucket, Policy=json.dumps(policy))
        except ClientError as e:
            logger.warning("Could not set public-read policy on results/*: %s", e)

    def check_storage_health(self) -> bool:
        """
        Verifies MinIO reachability and ensures the app bucket exists.
        Returns False on failure without raising (startup-safe).
        """
        try:
            self.ensure_bucket()
            return True
        except Exception as e:
            logger.error("Object storage health check failed: %s", e)
            return False

    def upload_document(
        self,
        file_id: UUID,
        file_obj: Union[BinaryIO, bytes],
        filename: str,
        content_type: str = "application/pdf",
    ) -> str:
        """
        Uploads a raw PDF document file to MinIO bucket.
        Returns the object key (storage_path) of the stored object.
        """
        safe_name = filename.replace("\\", "/").split("/")[-1] or "document.pdf"
        storage_path = f"documents/{file_id}/{safe_name}"
        body: BinaryIO
        if isinstance(file_obj, (bytes, bytearray)):
            body = BytesIO(file_obj)
        else:
            body = file_obj
            if hasattr(body, "seek"):
                body.seek(0)

        self.client.upload_fileobj(
            body,
            self.bucket,
            storage_path,
            ExtraArgs={"ContentType": content_type},
        )
        logger.info("Uploaded document to s3://%s/%s", self.bucket, storage_path)
        return storage_path

    def download_document(self, storage_path: str) -> bytes:
        """Retrieves the raw PDF binary contents from the storage."""
        buffer = BytesIO()
        self.client.download_fileobj(self.bucket, storage_path, buffer)
        return buffer.getvalue()

    def upload_result(self, job_id: UUID, content: str, file_format: str = "json") -> str:
        """Saves the structured OCR output (JSON, Markdown, or plain text) in object storage."""
        ext = "md" if file_format == "markdown" else file_format
        content_type = {
            "markdown": "text/markdown",
            "txt": "text/plain",
        }.get(file_format, "application/json")
        storage_path = f"results/{job_id}/result.{ext}"
        self.client.put_object(
            Bucket=self.bucket,
            Key=storage_path,
            Body=content.encode("utf-8"),
            ContentType=content_type,
        )
        logger.info("Uploaded result to s3://%s/%s", self.bucket, storage_path)
        return storage_path

    def upload_result_binary(
        self, job_id: UUID, content: bytes, filename: str, content_type: str
    ) -> str:
        """Saves a binary result artifact (e.g. a generated PDF) in object storage."""
        storage_path = f"results/{job_id}/{filename}"
        self.client.put_object(
            Bucket=self.bucket,
            Key=storage_path,
            Body=content,
            ContentType=content_type,
        )
        logger.info("Uploaded result to s3://%s/%s", self.bucket, storage_path)
        return storage_path

    def generate_presigned_url(
        self,
        storage_path: str,
        expiration: int = 3600,
        method: str = "get_object",
    ) -> str:
        """
        Generate a time-limited URL for downloading (or uploading) an object.
        method: 'get_object' | 'put_object'
        """
        return self.client.generate_presigned_url(
            ClientMethod=method,
            Params={"Bucket": self.bucket, "Key": storage_path},
            ExpiresIn=expiration,
        )

    def delete_document(self, storage_path: str) -> bool:
        """Deletes the document from the storage server."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=storage_path)
            logger.info("Deleted s3://%s/%s", self.bucket, storage_path)
            return True
        except ClientError as e:
            logger.error("Failed to delete %s: %s", storage_path, e)
            return False

    def delete_prefix(self, prefix: str) -> int:
        """
        Deletes every object under a key prefix (e.g. "results/{job_id}/").
        Returns the number of objects deleted. Best-effort — used for cleanup
        when a document/job is removed, never on the critical path.
        """
        deleted = 0
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if not keys:
                    continue
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
                deleted += len(keys)
        except ClientError as e:
            logger.error("Failed to delete prefix %s: %s", prefix, e)
        return deleted


# Initialize a default global instance of the storage service
storage_service = StorageService()
