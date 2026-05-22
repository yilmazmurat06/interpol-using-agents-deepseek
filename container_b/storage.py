"""
MinIO object storage client.

Stores raw notice payloads as JSON blobs and provides retrieval.
Bucket is auto-created on startup if missing.
"""

import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger("storage")


class MinioStorage:
    """MinIO client for storing raw Interpol notice payloads."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        secure: bool = False,
    ):
        self._endpoint = endpoint or os.environ.get("MINIO_ENDPOINT", "minio:9000")
        self._access_key = access_key or os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
        self._secret_key = secret_key or os.environ.get("MINIO_SECRET_KEY", "minioadmin")
        self._bucket_name = bucket_name or os.environ.get("MINIO_BUCKET", "interpol-notices")
        self._secure = secure

        self._client: Optional[Minio] = None

    def connect(self):
        """Establish connection and ensure bucket exists."""
        self._client = Minio(
            self._endpoint,
            access_key=self._access_key,
            secret_key=self._secret_key,
            secure=self._secure,
        )

        # Auto-create bucket if missing
        try:
            found = self._client.bucket_exists(self._bucket_name)
            if not found:
                self._client.make_bucket(self._bucket_name)
                logger.info("Created MinIO bucket '%s'", self._bucket_name)
            else:
                logger.info("MinIO bucket '%s' already exists", self._bucket_name)
        except S3Error as exc:
            logger.warning("MinIO bucket check failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Storage operations
    # ------------------------------------------------------------------

    def store_payload(self, notice_id: str, payload: Dict[str, Any]) -> Optional[str]:
        """
        Store a raw notice payload as a JSON blob.

        Object key: notices/<notice_id>/<iso_timestamp>.json

        Returns the object key on success, None on failure.
        """
        if self._client is None:
            self.connect()

        # Replace slashes in notice_id for filesystem safety
        safe_id = notice_id.replace("/", "-")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        object_name = f"notices/{safe_id}/{timestamp}.json"

        try:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self._client.put_object(
                self._bucket_name,
                object_name,
                data=io.BytesIO(data),
                length=len(data),
                content_type="application/json",
            )
            logger.debug("Stored payload for %s → %s", notice_id, object_name)
            return object_name
        except Exception:
            logger.exception("Failed to store payload for %s", notice_id)
            return None

    def get_payload(self, notice_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the most recent stored payload for a notice.

        Returns the parsed JSON payload, or None.
        """
        if self._client is None:
            self.connect()

        safe_id = notice_id.replace("/", "-")
        prefix = f"notices/{safe_id}/"

        try:
            objects = list(self._client.list_objects(
                self._bucket_name, prefix=prefix, recursive=True
            ))
        except S3Error:
            logger.exception("Failed to list objects for %s", notice_id)
            return None

        if not objects:
            return None

        # Pick the most recently modified object
        latest = max(objects, key=lambda o: o.last_modified)
        try:
            response = self._client.get_object(self._bucket_name, latest.object_name)
            data = response.read()
            response.close()
            response.release_conn()
            return json.loads(data.decode("utf-8"))
        except Exception:
            logger.exception("Failed to read payload for %s", notice_id)
            return None
