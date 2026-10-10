"""Forward POST /agent/message to the EcoLink agent (agent/lambda_handler.py).

Target, from the environment:
  AGENT_FUNCTION_NAME   invoke that Lambda directly (what the CDK stack configures)
  AGENT_URL             or POST JSON to an HTTP endpoint
If neither is set the call fails with 503 `not_configured` -- there is no fake agent.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ecolink.core.http import ApiError

TIMEOUT_SECONDS = 55


def call_agent(payload: dict[str, Any]) -> dict[str, Any]:
    function_name = os.environ.get("AGENT_FUNCTION_NAME")
    url = os.environ.get("AGENT_URL")
    if function_name:
        status, body = _invoke_lambda(function_name, payload)
    elif url:
        status, body = _post_http(url, payload)
    else:
        raise ApiError(503, "not_configured", "Set AGENT_FUNCTION_NAME or AGENT_URL to reach the agent")

    if 200 <= status < 300:
        return body
    detail = body.get("detail") or body.get("error") or body.get("message") or "agent error"
    if 400 <= status < 500:
        raise ApiError(status, "upstream_rejected", str(detail))
    raise ApiError(502, "upstream_error", f"agent failed: {detail}")


def _invoke_lambda(function_name: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    import boto3

    event = {"httpMethod": "POST", "body": json.dumps(payload)}
    try:
        resp = boto3.client("lambda").invoke(
            FunctionName=function_name, InvocationType="RequestResponse", Payload=json.dumps(event)
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, "upstream_error", f"Could not invoke the agent: {exc}") from exc
    if resp.get("FunctionError"):
        raise ApiError(502, "upstream_error", "The agent function raised an error")
    result = json.loads(resp["Payload"].read() or b"{}")
    return int(result.get("statusCode", 200)), _loads(result.get("body", result))


def _post_http(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if not url.startswith(("https://", "http://")):
        raise ApiError(502, "upstream_error", "AGENT_URL must be http(s)")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
            return resp.status, _loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _loads(exc.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ApiError(502, "upstream_error", f"Agent unreachable: {exc}") from exc


def _loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {"detail": "non-JSON response from the agent"}
    return data if isinstance(data, dict) else {"detail": "unexpected response from the agent"}
