"""Secret lookup with a short in-memory cache (per warm Lambda).

SECRETS_BACKEND selects where secrets live:
  secretsmanager  AWS Secrets Manager (default)
  ssm             SSM Parameter Store SecureString parameters, for accounts where Secrets
                  Manager is not available. Names are stored with a leading slash.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

_TTL_SECONDS = 300
_cache: dict[str, tuple[float, str]] = {}


def backend() -> str:
    return os.environ.get("SECRETS_BACKEND", "secretsmanager").lower()


def ssm_name(name: str) -> str:
    return "/" + name.lstrip("/")


def get_secret_string(name: str) -> str:
    cached = _cache.get(name)
    if cached and time.monotonic() - cached[0] < _TTL_SECONDS:
        return cached[1]
    import boto3  # lazy: keeps pure modules importable without AWS libs

    if backend() == "ssm":
        value = boto3.client("ssm").get_parameter(Name=ssm_name(name), WithDecryption=True)["Parameter"]["Value"]
    else:
        value = boto3.client("secretsmanager").get_secret_value(SecretId=name)["SecretString"]
    _cache[name] = (time.monotonic(), value)
    return value


def get_secret_json(name: str) -> dict[str, Any]:
    return json.loads(get_secret_string(name))
