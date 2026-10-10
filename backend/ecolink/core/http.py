"""API Gateway (REST, Lambda proxy) helpers: responses, error mapping, body parsing.

Error bodies are always {"error": <code>, "detail": <message>}. "detail" is what
agent/api_client.py surfaces to the agent, so keep it human-readable.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import os
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import urlencode

from ecolink.core.log import log


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(f"{status} {code}: {detail}")


def bad_request(detail: str, code: str = "invalid_request") -> ApiError:
    return ApiError(400, code, detail)


def not_found(detail: str) -> ApiError:
    return ApiError(404, "not_found", detail)


def conflict(code: str, detail: str) -> ApiError:
    return ApiError(409, code, detail)


def json_default(value: Any) -> Any:
    """DynamoDB returns Decimal and set; make them JSON-friendly."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"Not JSON serialisable: {type(value).__name__}")


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
    }


def respond(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_cors_headers()},
        "body": json.dumps(body, default=json_default, ensure_ascii=False),
    }


def raw_response(status: int, content_type: str, body: str) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": content_type}, "body": body}


def api_handler(fn: Callable[[dict, Any], Any]) -> Callable[[dict, Any], dict]:
    """Wrap a handler: return a dict body for 200, or a full proxy response; map errors."""

    @functools.wraps(fn)
    def wrapper(event: dict, context: Any) -> dict:
        request_id = getattr(context, "aws_request_id", "local")
        try:
            result = fn(event, context)
            if isinstance(result, dict) and "statusCode" in result:
                return result
            return respond(200, result)
        except ApiError as exc:
            log("api_error", logging.WARNING, status=exc.status, code=exc.code,
                detail=exc.detail, request_id=request_id, route=event.get("resource"))
            return respond(exc.status, {"error": exc.code, "detail": exc.detail})
        except Exception:  # noqa: BLE001 - last-resort guard, details go to the logs only
            logging.getLogger("ecolink").exception("unhandled_error request_id=%s", request_id)
            return respond(500, {"error": "internal_error", "detail": "Unexpected server error"})

    return wrapper


# ── Request parsing ─────────────────────────────────────────────────────────────

def raw_body(event: dict) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded") and body:
        return base64.b64decode(body).decode("utf-8", errors="replace")
    return body


def json_body(event: dict) -> dict[str, Any]:
    text = raw_body(event)
    if not text.strip():
        raise bad_request("Request body is required")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise bad_request("Body is not valid JSON") from None
    if not isinstance(data, dict):
        raise bad_request("Body must be a JSON object")
    return data


def query_params(event: dict) -> dict[str, str]:
    return event.get("queryStringParameters") or {}


def path_param(event: dict, name: str) -> str | None:
    return (event.get("pathParameters") or {}).get(name)


def is_cognito_caller(event: dict) -> bool:
    """True when API Gateway authenticated the caller with the Cognito user pool (dashboard)."""
    return bool(((event.get("requestContext") or {}).get("authorizer") or {}).get("claims"))


def cognito_groups(event: dict) -> set[str]:
    """Groups of a dashboard (Cognito) caller; empty for API-key callers."""
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    raw = claims.get("cognito:groups", "")
    if isinstance(raw, list):
        return {str(g) for g in raw}
    return {g for g in str(raw).strip("[]").replace(",", " ").split() if g}


def request_url(event: dict) -> str:
    """The public URL the caller used (needed to verify provider signatures).

    PUBLIC_API_BASE_URL (e.g. https://api.example.com/v1) overrides discovery; use it
    behind a custom domain.
    """
    ctx = event.get("requestContext") or {}
    base = os.environ.get("PUBLIC_API_BASE_URL", "").rstrip("/")
    if base:
        url = base + (event.get("path") or "")
    else:
        url = f"https://{ctx.get('domainName', 'localhost')}{ctx.get('path') or event.get('path') or ''}"
    qs = event.get("queryStringParameters")
    return f"{url}?{urlencode(qs)}" if qs else url


def public_base_url(event: dict) -> str:
    """Base URL of this API incl. stage, e.g. https://abc.execute-api.ap-south-1.amazonaws.com/prod."""
    base = os.environ.get("PUBLIC_API_BASE_URL", "").rstrip("/")
    if base:
        return base
    ctx = event.get("requestContext") or {}
    return f"https://{ctx.get('domainName', 'localhost')}/{ctx.get('stage', '')}".rstrip("/")
