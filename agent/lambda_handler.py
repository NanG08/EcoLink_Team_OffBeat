"""
AWS Lambda handler for POST /agent/message.

Expected event body (API Gateway proxy integration):
    {
      "user_id":   "whatsapp:+91XXXXXXXXXX",
      "text":      "hello",
      "image_url": "https://...",   // optional
      "lang":      "en"             // optional hint; agent detects automatically
    }

Response shape:
    {
      "reply_text": "...",
      "actions": []
    }
"""

from __future__ import annotations

import json
import logging
import os

# Structured JSON logging for CloudWatch Insights
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Module-level agent – created once per Lambda container (warm-start reuse).
_agent_instance = None


def _get_agent():
    """Lazy-initialise the EcoLinkAgent singleton."""
    global _agent_instance
    if _agent_instance is None:
        from agent.agent_core import EcoLinkAgent
        _agent_instance = EcoLinkAgent()
    return _agent_instance


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Content-Type": "application/json",
    }


def _error_response(status_code: int, message: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": _cors_headers(),
        "body": json.dumps({"error": message}),
    }


def handler(event: dict, context: object) -> dict:
    """
    Lambda entry point.  Handles:
      - Warm-start (reuses module-level agent).
      - API Gateway proxy v1 (body is a JSON string).
      - Direct Lambda invocations (body is already a dict).
      - OPTIONS pre-flight for CORS.
    """
    # ── CORS pre-flight ──────────────────────────────────────────────────────
    http_method = event.get("httpMethod", "POST")
    if http_method == "OPTIONS":
        return {"statusCode": 200, "headers": _cors_headers(), "body": ""}

    # ── Parse body ───────────────────────────────────────────────────────────
    raw_body = event.get("body", event)  # direct invoke fallback
    if isinstance(raw_body, str):
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return _error_response(400, "Invalid JSON body")
    elif isinstance(raw_body, dict):
        body = raw_body
    else:
        return _error_response(400, "Unrecognised request format")

    user_id: str | None = body.get("user_id")
    text: str | None = body.get("text")
    image_url: str | None = body.get("image_url")

    if not user_id or not text:
        return _error_response(400, "user_id and text are required")

    logger.info(
        json.dumps(
            {
                "event": "lambda_invoked",
                "user_id": user_id,
                "has_image": bool(image_url),
                "request_id": getattr(context, "aws_request_id", "local"),
            }
        )
    )

    # ── Invoke agent ─────────────────────────────────────────────────────────
    try:
        reply_text = _get_agent().handle(
            user_id=user_id,
            text=text,
            image_url=image_url,
        )
    except Exception as exc:
        logger.error(
            json.dumps(
                {
                    "event": "handler_error",
                    "user_id": user_id,
                    "error": str(exc),
                }
            )
        )
        reply_text = "Sorry, I couldn't process your message right now. Please try again."

    response_body = {
        "reply_text": reply_text,
        "actions": [],  # Extend here for rich WhatsApp action buttons
    }

    return {
        "statusCode": 200,
        "headers": _cors_headers(),
        "body": json.dumps(response_body, ensure_ascii=False),
    }
