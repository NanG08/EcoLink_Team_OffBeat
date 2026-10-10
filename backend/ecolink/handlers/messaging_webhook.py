"""POST /webhooks/messaging -- inbound chat messages (e.g. WhatsApp).

Flow: verify signature -> parse -> (store any photo privately in S3) -> forward to the
agent (same code path as POST /agent/message) -> send the reply back through the
MessagingProvider. Replies are sent via the provider API rather than in the webhook
response, so a slow agent never breaks the provider's webhook timeout semantics.
"""

from __future__ import annotations

import logging
from typing import Any

from ecolink import deps
from ecolink.core.http import ApiError, api_handler, bad_request, raw_response
from ecolink.core.log import log
from ecolink.core.storage import ALLOWED_IMAGE_TYPES, MAX_IMAGE_BYTES
from ecolink.handlers.proxy import agent_message
from ecolink.providers.base import InboundMessage, ProviderError, WebhookRequest, invalid_signature

_FALLBACK_REPLY = {
    "en": "Sorry, I couldn't process your message right now. Please try again in a minute.",
    "hi": "माफ़ करें, अभी आपका संदेश संसाधित नहीं हो सका। कृपया थोड़ी देर बाद फिर कोशिश करें।",
}


def detect_lang(text: str) -> str:
    """Same heuristic as the agent: >10% Devanagari characters means Hindi."""
    devanagari = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    return "hi" if devanagari / max(len(text), 1) > 0.10 else "en"


@api_handler
def handler(event: dict, context: Any) -> dict[str, Any]:
    messaging = deps.get_messaging()
    request = WebhookRequest.from_event(event)
    if not messaging.verify_request(request):
        raise invalid_signature()
    msg = messaging.parse_inbound(request)
    if not msg.user_id:
        raise bad_request("Could not determine the sender")

    lang = detect_lang(msg.text)
    log("inbound_message", user_id=msg.user_id, has_media=bool(msg.media_url),
        has_location=msg.latitude is not None, message_id=msg.message_id)

    _record_user(msg, lang)
    image_url = _store_image(msg)
    text = msg.text or ("(photo)" if image_url else "(location shared)")
    location = _known_location(msg)
    if location:  # the agent has no other source of coordinates for find_recyclers
        text = f"[USER_LOCATION: lat={location[0]} lng={location[1]}]" + chr(10) + text

    try:
        payload = {"user_id": msg.user_id, "text": text, "lang": lang}
        if image_url:
            payload["image_url"] = image_url
        reply = agent_message(payload).get("reply_text") or _FALLBACK_REPLY[lang]
    except ApiError as exc:
        log("agent_forward_failed", logging.ERROR, user_id=msg.user_id, code=exc.code)
        reply = _FALLBACK_REPLY[lang]

    try:
        messaging.send_text(messaging.address_for(msg.user_id), reply)
    except ProviderError as exc:
        log("reply_send_failed", logging.ERROR, user_id=msg.user_id, error=str(exc))
    content_type, body = messaging.webhook_ack()
    return raw_response(200, content_type, body)


def _record_user(msg: InboundMessage, lang: str) -> None:
    """Best-effort: remember the user and any shared WhatsApp location."""
    try:
        repo = deps.get_repo()
        repo.ensure_user(msg.user_id, lang)
        if msg.latitude is not None and msg.longitude is not None:
            repo.set_user_location(msg.user_id, msg.latitude, msg.longitude)
    except Exception:  # noqa: BLE001
        logging.getLogger("ecolink").exception("user_record_failed user_id=%s", msg.user_id)


def _known_location(msg: InboundMessage) -> tuple[float, float] | None:
    """Location shared in this message, else the one saved for the user earlier."""
    if msg.latitude is not None and msg.longitude is not None:
        return msg.latitude, msg.longitude
    try:
        user = deps.get_repo().get_user(msg.user_id) or {}
        return float(user["lat"]), float(user["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    except Exception:  # noqa: BLE001
        logging.getLogger("ecolink").exception("location_lookup_failed user_id=%s", msg.user_id)
        return None


def _store_image(msg: InboundMessage) -> str | None:
    """Copy a photo into the private bucket and return a time-limited pre-signed URL."""
    if not msg.media_url:
        return None
    try:
        media = deps.get_messaging().fetch_media(msg.media_url)
        if media is None:  # provider hands out URLs that are already usable (console fake)
            return msg.media_url
        data, content_type = media
        if content_type not in ALLOWED_IMAGE_TYPES or len(data) > MAX_IMAGE_BYTES:
            log("media_rejected", user_id=msg.user_id, content_type=content_type, size=len(data))
            return None
        store = deps.get_image_store()
        url = store.presigned_get(store.put_image(msg.user_id, data, content_type))
        try:  # our private, 7-day copy is the only one that should exist
            deps.get_messaging().delete_media(msg.media_url)
        except ProviderError as exc:
            log("media_delete_failed", logging.WARNING, user_id=msg.user_id, error=str(exc))
        return url
    except Exception:  # noqa: BLE001 - a bad photo must not block the conversation
        logging.getLogger("ecolink").exception("media_store_failed user_id=%s", msg.user_id)
        return None
