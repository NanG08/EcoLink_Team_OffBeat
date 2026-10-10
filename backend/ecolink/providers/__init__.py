"""Provider factory. Handlers depend on the MessagingProvider / VoiceProvider interfaces
(providers/base.py); this is the single place that picks the implementation.

Only the Twilio adapter ships today. To swap in another vendor (e.g. AWS End User
Messaging for WhatsApp, Amazon Connect for voice), implement the ABCs, add a branch here
and set MESSAGING_PROVIDER / VOICE_PROVIDER.
"""

from __future__ import annotations

import os

from ecolink.providers.base import MessagingProvider, VoiceProvider


def build_messaging_provider(name: str | None = None) -> MessagingProvider:
    name = (name or os.environ.get("MESSAGING_PROVIDER", "twilio")).lower()
    if name == "twilio":
        from ecolink.providers.twilio import TwilioMessagingProvider
        return TwilioMessagingProvider()
    raise ValueError(f"Unknown MESSAGING_PROVIDER '{name}' (supported: twilio)")


def build_voice_provider(name: str | None = None) -> VoiceProvider:
    name = (name or os.environ.get("VOICE_PROVIDER", "twilio")).lower()
    if name == "twilio":
        from ecolink.providers.twilio import TwilioVoiceProvider
        return TwilioVoiceProvider()
    raise ValueError(f"Unknown VOICE_PROVIDER '{name}' (supported: twilio)")
