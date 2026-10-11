"""Voice routes.

  POST /voice/offer     (API key)   {pickup_id, recycler_id}  -> phone the recycler
  POST /voice/response  (provider)  {pickup_id, digit}        -> 1 = accept, 2 = decline

/voice/response has no API-key authorizer in API Gateway because the voice provider calls
it. It accepts two kinds of caller:
  * the voice provider (Twilio): signed form callback, verified by VoiceProvider.verify_request;
  * any client holding the API key (`Authorization: Bearer`): the JSON contract body
    {pickup_id, digit}, e.g. another voice platform or a manual fallback during a demo.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ecolink import deps
from ecolink.core.http import (
    ApiError,
    api_handler,
    json_body,
    public_base_url,
    raw_response,
)
from ecolink.handlers.authorizer import token_is_valid
from ecolink.core.validation import require_str
from ecolink.providers.base import WebhookRequest, invalid_signature


@api_handler
def handler(event: dict, context: Any) -> Any:
    route = event.get("resource") or event.get("path", "")
    if route.endswith("/voice/offer"):
        return _offer(event)
    if route.endswith("/voice/response"):
        return _response(event)
    raise ApiError(404, "not_found", "Unknown route")


def _offer(event: dict) -> dict[str, Any]:
    data = json_body(event)
    pickup_id = require_str(data, "pickup_id", 64)
    recycler_id = require_str(data, "recycler_id", 64)
    callback_url = f"{public_base_url(event)}/voice/response?pickup_id={quote(pickup_id, safe='')}"
    pickup, call_id = deps.get_service().offer(pickup_id, recycler_id, callback_url)
    return {
        "ok": True,
        "pickup_id": pickup["pickup_id"],
        "recycler_id": recycler_id,
        "status": pickup["status"],
        "call_id": call_id,
    }


def _response(event: dict) -> dict[str, Any]:
    request = WebhookRequest.from_event(event)
    authorization = request.headers.get("authorization")
    if authorization:  # JSON contract caller, authenticated with the API key
        if not token_is_valid(authorization):
            raise ApiError(401, "unauthorized", "Invalid API key")
        data = json_body(event)
        pickup = deps.get_service().record_recycler_response(
            require_str(data, "pickup_id", 64), str(data.get("digit", "")).strip())
        return {"pickup_id": pickup["pickup_id"], "status": pickup["status"]}

    voice = deps.get_voice()
    if not voice.verify_request(request):
        raise invalid_signature(request)
    parsed = voice.parse_response(request)
    pickup = deps.get_service().record_recycler_response(parsed.pickup_id, parsed.digit)
    content_type, body = voice.ack({"pickup_id": pickup["pickup_id"], "status": pickup["status"]})
    return raw_response(200, content_type, body)
