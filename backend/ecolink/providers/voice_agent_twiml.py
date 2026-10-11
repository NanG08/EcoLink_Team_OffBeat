"""
TwiML builders for the EcoLink conversational voice agent.

Twilio calls POST /voice/agent when a user phones the EcoLink number.
Each HTTP round-trip is one turn of the conversation:

  Twilio → POST /voice/agent/start    (new call)
         ← TwiML: greet + <Gather speech>

  Twilio → POST /voice/agent/turn     (user spoke)
         ← TwiML: agent reply via <Say> + next <Gather speech>

  Twilio → POST /voice/agent/end      (call ended / hangup)
         ← TwiML: <Hangup>

All TwiML is built here; no vendor imports required — just stdlib xml.sax.saxutils
for escaping and plain string construction.

Language support
----------------
Hindi (hi) → Polly voice "Aditi", language tag "hi-IN"
English    → Polly voice "Raveena", language tag "en-IN"

The <Say> verb uses Amazon Polly neural voices via Twilio's built-in
voice= attribute (format: "Polly.<VoiceName>").
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

# ──────────────────────────────────────────────────────────────────────────────
# Voice / language config
# ──────────────────────────────────────────────────────────────────────────────

_VOICES = {
    "en": {"voice": "Polly.Raveena", "language": "en-IN"},
    "hi": {"voice": "Polly.Aditi",   "language": "hi-IN"},
}

_GREETING = {
    "en": (
        "Welcome to EcoLink, your waste recycling assistant. "
        "You can tell me what waste you have, and I will help you sell or recycle it. "
        "Please speak after the beep."
    ),
    "hi": (
        "नमस्ते, EcoLink में आपका स्वागत है। "
        "कृपया अपना कचरा बताएं और हम उसे बेचने या रीसायकल करने में मदद करेंगे। "
        "बीप के बाद बोलें।"
    ),
}

_SILENCE_NUDGE = {
    "en": "I didn't catch that. Please speak after the beep.",
    "hi": "मुझे सुनाई नहीं दिया। कृपया बीप के बाद बोलें।",
}

_GOODBYE = {
    "en": "Thank you for calling EcoLink. Goodbye!",
    "hi": "EcoLink से संपर्क करने के लिए धन्यवाद। नमस्ते!",
}

_ERROR = {
    "en": "Sorry, something went wrong. Please try again later.",
    "hi": "माफ़ करें, कुछ गड़बड़ हो गई। थोड़ी देर बाद फिर कोशिश करें।",
}

# Twilio speech recognition: recognise both languages so bilingual users work.
_SPEECH_MODEL = "phone_call"
_SPEECH_TIMEOUT = "3"          # seconds of silence before Twilio sends the turn
_SPEECH_MAX_SPEECH = "15"      # max recording duration per turn


# ──────────────────────────────────────────────────────────────────────────────
# Low-level TwiML helpers
# ──────────────────────────────────────────────────────────────────────────────

def _say(text: str, lang: str) -> str:
    """<Say> element with Polly neural voice."""
    cfg = _VOICES.get(lang, _VOICES["en"])
    return (
        f'<Say voice={quoteattr(cfg["voice"])} language={quoteattr(cfg["language"])}>'
        f"{escape(text)}"
        f"</Say>"
    )


def _gather(action_url: str, lang: str, inner: str = "") -> str:
    """
    <Gather> with speech input.

    ``inner`` is optional TwiML spoken *while* gathering (e.g. a <Say>).
    After the user stops speaking (or silence timeout), Twilio POSTs to action_url
    with ``SpeechResult`` in the form body.
    """
    cfg = _VOICES.get(lang, _VOICES["en"])
    action = quoteattr(action_url)
    lang_tag = quoteattr(cfg["language"])
    return (
        f'<Gather input="speech" action={action} method="POST" '
        f'language={lang_tag} speechModel={quoteattr(_SPEECH_MODEL)} '
        f'speechTimeout={quoteattr(_SPEECH_TIMEOUT)} '
        f'speechMaxSpeech={quoteattr(_SPEECH_MAX_SPEECH)}>'
        f"{inner}"
        f"</Gather>"
    )


def _response(*elements: str) -> str:
    """Wrap elements in a TwiML <Response> root."""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{"".join(elements)}</Response>'


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def greeting_twiml(turn_url: str, lang: str = "en") -> str:
    """
    TwiML for the very first turn: greet the caller and start listening.

    Args:
        turn_url: Absolute URL Twilio should POST to with the caller's speech.
        lang: "en" (default) or "hi".

    Returns:
        TwiML string (UTF-8 XML).
    """
    inner = _say(_GREETING[lang], lang)
    gather = _gather(turn_url, lang, inner)
    # Fallback if user says nothing: speak nudge and hang up gracefully.
    fallback = _say(_SILENCE_NUDGE[lang], lang)
    return _response(gather, fallback)


def reply_twiml(agent_reply: str, turn_url: str, lang: str = "en") -> str:
    """
    TwiML for a mid-conversation turn: speak the agent reply, then listen again.

    Args:
        agent_reply: Text returned by EcoLinkAgent.handle().
        turn_url: URL for the next turn POST.
        lang: Detected language of the current turn.

    Returns:
        TwiML string.
    """
    # Speak the agent reply *inside* the Gather so the user can interrupt (barge-in).
    inner = _say(agent_reply, lang)
    gather = _gather(turn_url, lang, inner)
    # Fallback if user stays silent after the reply: gentle nudge then hang up.
    nudge = _say(_SILENCE_NUDGE[lang], lang)
    return _response(gather, nudge)


def goodbye_twiml(lang: str = "en") -> str:
    """TwiML to end the call gracefully."""
    return _response(_say(_GOODBYE[lang], lang), "<Hangup/>")


def error_twiml(lang: str = "en") -> str:
    """TwiML to read when an unexpected backend error occurs."""
    return _response(_say(_ERROR[lang], lang), "<Hangup/>")


def transfer_twiml(phone_number: str, lang: str = "en") -> str:
    """
    TwiML to transfer the call to a human operator or recycler phone number.

    Not used in the main flow but wired as an escape hatch when the user
    says "talk to a person" / "operator" / "manuush se baat karo".
    """
    transfer_say = {
        "en": "Connecting you to our team now. Please hold.",
        "hi": "आपको हमारी टीम से जोड़ रहे हैं। कृपया प्रतीक्षा करें।",
    }
    return _response(
        _say(transfer_say.get(lang, transfer_say["en"]), lang),
        f"<Dial>{escape(phone_number)}</Dial>",
    )
