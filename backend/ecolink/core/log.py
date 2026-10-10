"""Structured JSON logging for CloudWatch Logs Insights. Never log addresses."""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("ecolink")
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def log(event: str, level: int = logging.INFO, **fields: object) -> None:
    logger.log(level, json.dumps({"event": event, **fields}, default=str))
