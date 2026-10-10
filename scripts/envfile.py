"""Tiny .env loader (no dependency): KEY=VALUE lines, '#' comments, optional quotes.

Real environment variables win over the file, and empty values are skipped (an empty
AWS_ACCESS_KEY_ID would otherwise break boto3's credential chain).
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path | str | None = None) -> dict[str, str]:
    """Load the file into os.environ; returns the variables it set."""
    env_file = Path(path) if path else DEFAULT_PATH
    applied: dict[str, str] = {}
    if not env_file.is_file():
        return applied
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
