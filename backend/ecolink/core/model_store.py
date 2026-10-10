"""Model artifacts (ONNX weights, fallback rate table) kept in a private S3 bucket.

The CDK stack uploads them to the models bucket (see infra/ecolink_stack.py). Lambda
downloads a file once per container into /tmp and reuses it.

Environment:
  MODELS_BUCKET        bucket name
  VISION_MODEL_KEY     key of the ONNX weights   (default vision/best.onnx)
  PRICING_TABLE_KEY    key of the rate table     (default pricing/rate_table.json)
  MODEL_VERSION        hash of the uploaded files, so a changed model is never served from a stale /tmp copy
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ecolink.core.log import log

VISION_MODEL_KEY = "vision/best.onnx"
PRICING_TABLE_KEY = "pricing/rate_table.json"


class ModelUnavailable(Exception):
    """The artifact is not configured, not in the bucket, or the runtime cannot load it."""


class ModelStore:
    def __init__(self, bucket: str | None = None, client: Any | None = None, cache_dir: str | None = None) -> None:
        self.bucket = bucket or os.environ.get("MODELS_BUCKET", "")
        if not self.bucket:
            raise ModelUnavailable("MODELS_BUCKET is not configured")
        if client is None:
            import boto3

            client = boto3.client("s3")
        self._s3 = client
        version = os.environ.get("MODEL_VERSION", "v0")
        self._dir = Path(cache_dir or os.environ.get("MODEL_CACHE_DIR", "/tmp/ecolink-models")) / version

    def local_path(self, key: str) -> Path:
        """Download `key` once and return the local file path."""
        target = self._dir / key.replace("/", "__")
        if target.is_file() and target.stat().st_size > 0:
            return target
        self._dir.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        try:
            self._s3.download_file(self.bucket, key, str(partial))
        except Exception as exc:  # noqa: BLE001 - botocore ClientError, endpoint errors, ...
            partial.unlink(missing_ok=True)
            raise ModelUnavailable(f"Could not download s3://{self.bucket}/{key}: {exc}") from exc
        partial.replace(target)
        log("model_downloaded", key=key, bytes=target.stat().st_size)
        return target

    def read_json(self, key: str) -> dict[str, Any]:
        try:
            return json.loads(self.local_path(key).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelUnavailable(f"s3://{self.bucket}/{key} is not valid JSON: {exc}") from exc
