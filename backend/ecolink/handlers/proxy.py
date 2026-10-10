"""POST /classify (Bedrock vision classifier) and POST /agent/message (the EcoLink agent)."""

from __future__ import annotations

from typing import Any

from ecolink.core import classifier, upstream
from ecolink.core.http import ApiError, api_handler, json_body
from ecolink.core.validation import optional_str, require_str


@api_handler
def handler(event: dict, context: Any) -> dict[str, Any]:
    route = event.get("resource") or event.get("path", "")
    if route.endswith("/classify"):
        return classify(json_body(event))
    if route.endswith("/agent/message"):
        return agent_message(json_body(event))
    raise ApiError(404, "not_found", "Unknown route")


def classify(data: dict[str, Any]) -> dict[str, Any]:
    return classifier.classify(require_str(data, "image_url", 4096))


def agent_message(data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": require_str(data, "user_id", 128),
        "text": require_str(data, "text", 4000),
        "lang": (optional_str(data, "lang", 8) or "en"),
    }
    image_url = optional_str(data, "image_url", 4096)
    if image_url:
        payload["image_url"] = image_url
    result = upstream.call_agent(payload)
    if "reply_text" not in result:
        raise ApiError(502, "upstream_error", "Agent returned an unexpected response")
    result.setdefault("actions", [])
    return result
