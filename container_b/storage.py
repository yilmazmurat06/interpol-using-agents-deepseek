"""MinIO object storage for raw notice payloads.

Uses deterministic keys: notices/<notice_id>/<received_at>.json
"""

import io
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

MINIO_ENDPOINT: str = os.environ.get("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY: str = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY: str = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET: str = os.environ.get("MINIO_BUCKET", "interpol-notices")
MINIO_SECURE: bool = os.environ.get("MINIO_SECURE", "false").lower() in ("true", "1", "yes")


class MinIOStorage:
    """Stores raw notice payloads in MinIO.

    Bucket is auto-created on connect if missing.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket: Optional[str] = None,
        secure: Optional[bool] = None,
    ) -> None:
        self._endpoint: str = endpoint or MINIO_ENDPOINT
        self._access_key: str = access_key or MINIO_ACCESS_KEY
        self._secret_key: str = secret_key or MINIO_SECRET_KEY
        self._bucket: str = bucket or MINIO_BUCKET
        self._secure: bool = secure if secure is not None else MINIO_SECURE
        self._client: Optional[minio.Minio] = None

    def connect(self) -> None:
        """Connect to MinIO and ensure the bucket exists."""
        self._client = minio.Minio(
            self._endpoint,
            access_key=self._access_key,
            secret_key=self._secret_key,
            secure=self._secure,
        )
        self._ensure_bucket()
        logger.info(
            "MinIO connected: endpoint=%s secure=%s bucket=%s",
            self._endpoint, self._secure, self._bucket,
        )

    def close(self) -> None:
        """Release MinIO client resources (no-op — HTTP client is stateless)."""
        self._client = None
        logger.debug("MinIO storage closed.")

    def _ensure_bucket(self) -> None:
        """Create the bucket if it does not exist."""
        try:
            found = self._client.bucket_exists(self._bucket)
            if not found:
                self._client.make_bucket(self._bucket)
                logger.info("MinIO bucket created: %s", self._bucket)
        except S3Error as exc:
            logger.error("MinIO bucket check failed: %s", exc)
            raise

    def store_payload(
        self, notice_id: str, payload: Dict[str, Any]
    ) -> Optional[str]:
        """Store a raw payload as JSON. Returns the object key or None on failure."""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            object_key = f"notices/{notice_id}/{timestamp}.json"
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            length = len(data)
            self._client.put_object(
                self._bucket,
                object_key,
                data=io.BytesIO(data),
                length=length,
                content_type="application/json",
            )
            logger.debug("Stored payload in MinIO: %s", object_key)
            return object_key
        except Exception:
            logger.exception("Failed to store payload in MinIO for %s", notice_id)
            return None
