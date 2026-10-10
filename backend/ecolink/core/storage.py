"""Private S3 image storage. Objects expire after 7 days (bucket lifecycle rule in CDK).

Keys are images/<sha256(user_id)[:16]>/<uuid>.<ext> so no phone number appears in S3 keys.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

ALLOWED_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
UPLOAD_URL_TTL_SECONDS = 300


class ImageStore:
    def __init__(self, bucket: str | None = None, client: Any | None = None) -> None:
        self.bucket = bucket or os.environ["IMAGES_BUCKET"]
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("s3", config=Config(signature_version="s3v4"))
        self._s3 = client
        self._get_ttl = int(os.environ.get("IMAGE_URL_TTL_SECONDS", "3600"))

    @staticmethod
    def _key(user_id: str, ext: str) -> str:
        owner = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        return f"images/{owner}/{uuid.uuid4().hex}.{ext}"

    def presigned_get(self, key: str) -> str:
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self._get_ttl
        )

    def presign_upload(self, user_id: str, content_type: str) -> dict[str, Any]:
        """Pre-signed POST limited to one key, one content type and <= 5 MB."""
        key = self._key(user_id, ALLOWED_IMAGE_TYPES[content_type])
        post = self._s3.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_IMAGE_BYTES]],
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )
        return {
            "upload_url": post["url"],
            "fields": post["fields"],
            "key": key,
            "image_url": self.presigned_get(key),  # usable for /classify once uploaded
            "expires_in": UPLOAD_URL_TTL_SECONDS,
        }

    def put_image(self, user_id: str, data: bytes, content_type: str) -> str:
        key = self._key(user_id, ALLOWED_IMAGE_TYPES[content_type])
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key
