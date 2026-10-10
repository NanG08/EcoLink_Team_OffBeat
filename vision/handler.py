"""
AWS Lambda-compatible handler for POST /classify using the YOLOv8n ML pipeline.

Public entry point:
    classify({"image_url": "https://..."}) → {"items": [...]}

Conforms to the same request/response shape as
backend/ecolink/handlers/proxy.py so the Strands agent can call either.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Warm Lambda container: load model at module import time.
# Failures are logged as warnings so the Lambda can still cold-start.
# ─────────────────────────────────────────────────────────────────────────────

_MODEL = None

try:
    from ml.vision.model_loader import load_model

    _inf_cfg_path = Path(__file__).parent / "config" / "inference.yaml"
    _inf_cfg = yaml.safe_load(_inf_cfg_path.read_text(encoding="utf-8"))
    _weights = _inf_cfg["model"]["weights_path"]
    _device = _inf_cfg["model"]["device"]
    _MODEL = load_model(weights_path=_weights, device=_device)
except Exception as _exc:  # noqa: BLE001
    logger.warning("Model pre-load failed at import time: %s", _exc)


# ─────────────────────────────────────────────────────────────────────────────
# Direct callable (no Lambda infrastructure)
# ─────────────────────────────────────────────────────────────────────────────


def classify(request: dict) -> dict:
    """
    Main entry point for the vision pipeline.

    Args:
        request: Dict with at least ``{"image_url": str}``.

    Returns:
        ``{"items": [{"label": str, "confidence": float, "hazardous": bool}]}``

    Raises:
        ValueError: If ``image_url`` is missing or empty.
        InferenceError (from ml.vision.inference): URL / format violations.
    """
    image_url: str = request.get("image_url", "").strip()
    if not image_url:
        raise ValueError("image_url is required")

    from ml.vision.inference import classify_image_url  # lazy — avoids circular at import

    return classify_image_url(image_url)


def handler(image_url: str) -> dict:
    """
    Direct callable (for testing without Lambda infrastructure).

    Returns the same dict as :func:`classify`.
    """
    from ml.vision.inference import classify_image_url

    return classify_image_url(image_url)


# ─────────────────────────────────────────────────────────────────────────────
# AWS Lambda handler
# ─────────────────────────────────────────────────────────────────────────────


def lambda_handler(event: dict, context) -> dict:
    """
    AWS API Gateway Lambda proxy handler.

    Accepts both ``application/json`` body and direct dict invocations.
    """
    try:
        body_raw = event.get("body") or "{}"
        if isinstance(body_raw, str):
            body = json.loads(body_raw)
        else:
            body = body_raw

        image_url: str = body.get("image_url", "").strip()
        if not image_url:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "image_url is required"}),
            }

        result = handler(image_url)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("classify handler error")
        status = getattr(exc, "status_code", 500)
        return {
            "statusCode": status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(exc)}),
        }
