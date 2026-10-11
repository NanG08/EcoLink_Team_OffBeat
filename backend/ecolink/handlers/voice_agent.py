"""
Conversational voice agent for EcoLink.

Handles inbound phone calls from users (not recycler dispatch calls —
that is handled by handlers/voice.py).

Routes
------
  POST /voice/agent         — Twilio inbound call webhook (new call or turn)
  POST /voice/agent/end     — Twilio status callback (call ended / no-answer)

Flow
----
  1. User calls the EcoLink Twilio phone number.
  2. Twilio POSTs to POST /voice/agent with CallSid, empty SpeechResult (first turn).
  3. Handler returns a TwiML greeting + <Gather speech>.
  4. User speaks. Twilio transcribes and POSTs to POST /voice/agent again with SpeechResult.
  5. Handler passes SpeechResult → EcoLinkAgent.handle() → agent reply text.
  6. Handler returns TwiML: <Say agent_reply> + <Gather speech> for the next turn.
  7. Repeat until user hangs up or says "goodbye" / "band karo".

State
-----
  The existing ConversationState DynamoDB table is re-used.
  Voice user IDs are prefixed "voice:" so they never collide with WhatsApp IDs.
  e.g. voice:+919800000001

Security
--------
  Twilio signature verification is performed on every request via the existing
  TwilioVoiceProvider.verify_request() path. Requests without a valid
  X-Twilio-Signature are rejected with 403.

The handler intentionally does NOT use @api_handler so it can return TwiML
(text/xml) rather than JSON.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from ecolink.core.http import raw_response, request_url, public_base_url
from ecolink.core.log import log
from ecolink.providers.base import WebhookRequest, invalid_signature
from ecolink.providers.voice_agent_twiml import (
    error_twiml,
    goodbye_twiml,
    greeting_twiml,
    reply_twiml,
    transfer_twiml,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

_GOODBYE_PATTERN = re.compile(
    r"\b(bye|goodbye|stop|end|quit|hang up|close|band karo|बंद|अलविदा|बाय)\b",
    re.IGNORECASE | re.UNICODE,
)

_TRANSFER_PATTERN = re.compile(
    r"\b(operator|human|person|agent|representative|insaan|manushya|मनुष्य|इंसान)\b",
    re.IGNORECASE | re.UNICODE,
)

_MAX_SPEECH_CHARS = 500   # truncate absurdly long SpeechResult before passing to agent


# ──────────────────────────────────────────────────────────────────────────────
# Lazy agent singleton (re-used across warm Lambda invocations)
# ──────────────────────────────────────────────────────────────────────────────

_agent_instance = None


def _get_agent():
    global _agent_instance
    if _agent_instance is None:
        from agent.agent_core import EcoLinkAgent
        _agent_instance = EcoLinkAgent()
    return _agent_instance


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _twiml_response(twiml: str, status: int = 200) -> dict:
    return raw_response(status, "text/xml; charset=utf-8", twiml)


def _voice_user_id(call_sid: str, caller: str) -> str:
    """
    Stable user ID for a voice caller.

    The caller's E.164 phone number is used so the DynamoDB conversation state
    is shared across multiple calls from the same person (same as WhatsApp users).
    Falls back to the CallSid if the caller number isn't available.
    """
    number = caller.lstrip("+ ").strip()
    if number and number != "anonymous":
        return f"voice:+{number.lstrip('+')}"
    return f"voice:call:{call_sid}"


def _detect_lang(text: str) -> str:
    """Heuristic: >10% Devanagari → Hindi."""
    if not text:
        return "en"
    deva = sum(1 for ch in text if "\u0900" <= ch <= "\u097f")
    return "hi" if deva / len(text) > 0.10 else "en"


def _turn_url(base: str) -> str:
    """URL Twilio posts subsequent speech turns to."""
    return f"{base.rstrip('/')}/voice/agent"


# ──────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ──────────────────────────────────────────────────────────────────────────────

def handler(event: dict, context: Any) -> dict:
    """
    AWS Lambda / API Gateway proxy handler for the conversational voice agent.

    Handles both:
      • New inbound call  (no SpeechResult in the form body)
      • Subsequent turns  (SpeechResult present in the form body)
    """
    path = event.get("resource") or event.get("path", "")

    # ── Status callback: call ended ─────────────────────────────────────────
    if path.endswith("/voice/agent/end"):
        _handle_end(event)
        return _twiml_response("<Response/>")

    # ── Main call/turn handler ───────────────────────────────────────────────
    return _handle_turn(event)


def _handle_end(event: dict) -> None:
    """Log call-ended events (status callbacks from Twilio)."""
    request = WebhookRequest.from_event(event)
    form = request.form()
    log(
        "voice_agent_call_ended",
        call_sid=form.get("CallSid"),
        caller=form.get("Caller"),
        call_status=form.get("CallStatus"),
        call_duration=form.get("CallDuration"),
    )


def _handle_turn(event: dict) -> dict:
    request = WebhookRequest.from_event(event)

    # ── Twilio signature verification ────────────────────────────────────────
    from ecolink import deps
    voice = deps.get_voice()
    if not voice.verify_request(request):
        log("voice_agent_signature_failed", url=request.url)
        return raw_response(403, "text/xml", "<Response><Hangup/></Response>")

    form = request.form()
    call_sid: str = form.get("CallSid", "unknown")
    caller: str = form.get("Caller", form.get("From", ""))
    speech_result: str = form.get("SpeechResult", "").strip()[:_MAX_SPEECH_CHARS]
    confidence: str = form.get("Confidence", "")

    user_id = _voice_user_id(call_sid, caller)
    lang = _detect_lang(speech_result) if speech_result else "en"
    base_url = public_base_url(event)
    turn_url = _turn_url(base_url)

    log(
        "voice_agent_turn",
        call_sid=call_sid,
        user_id=user_id,
        has_speech=bool(speech_result),
        lang=lang,
        confidence=confidence,
    )

    # ── First turn: greet the caller ─────────────────────────────────────────
    if not speech_result:
        twiml = greeting_twiml(turn_url, lang)
        return _twiml_response(twiml)

    # ── Check for goodbye intent ─────────────────────────────────────────────
    if _GOODBYE_PATTERN.search(speech_result):
        log("voice_agent_goodbye", user_id=user_id, call_sid=call_sid)
        return _twiml_response(goodbye_twiml(lang))

    # ── Check for transfer-to-human request ─────────────────────────────────
    transfer_number = os.environ.get("VOICE_TRANSFER_NUMBER", "").strip()
    if transfer_number and _TRANSFER_PATTERN.search(speech_result):
        log("voice_agent_transfer", user_id=user_id, call_sid=call_sid)
        return _twiml_response(transfer_twiml(transfer_number, lang))

    # ── Run EcoLink agent ────────────────────────────────────────────────────
    try:
        agent = _get_agent()
        reply_text: str = agent.handle(
            user_id=user_id,
            text=speech_result,
            image_url=None,   # voice callers cannot send images; agent handles gracefully
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            json.dumps({
                "event": "voice_agent_error",
                "user_id": user_id,
                "call_sid": call_sid,
                "error": str(exc),
            })
        )
        return _twiml_response(error_twiml(lang))

    # ── Speak reply and listen for next turn ─────────────────────────────────
    twiml = reply_twiml(reply_text, turn_url, lang)
    return _twiml_response(twiml)
