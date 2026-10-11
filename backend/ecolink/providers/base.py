"""Provider interfaces. Handlers and services depend only on these, never on a vendor.

To add a provider (e.g. AWS End User Messaging / Amazon Connect): subclass the ABCs,
register the class in providers/__init__.py and set MESSAGING_PROVIDER / VOICE_PROVIDER.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

from ecolink.core.http import ApiError, bad_request, raw_body, request_url


class ProviderError(Exception):
    """A vendor call failed (network, auth, rejected request)."""


@dataclass
class WebhookRequest:
    """Vendor-neutral view of an inbound webhook call."""

    method: str
    url: str  # full public URL incl. query string (signatures are computed over it)
    headers: dict[str, str]  # lower-cased names
    query: dict[str, str]
    body: str

    @classmethod
    def from_event(cls, event: dict) -> "WebhookRequest":
        return cls(
            method=event.get("httpMethod", "POST"),
            url=request_url(event),
            headers={k.lower(): v for k, v in (event.get("headers") or {}).items()},
            query=event.get("queryStringParameters") or {},
            body=raw_body(event),
        )

    def form_pairs(self) -> list[tuple[str, str]]:
        return parse_qsl(self.body, keep_blank_values=True)

    def form(self) -> dict[str, str]:
        return dict(self.form_pairs())

    def json(self) -> dict[str, Any]:
        try:
            data = json.loads(self.body or "{}")
        except json.JSONDecodeError:
            raise bad_request("Body is not valid JSON") from None
        if not isinstance(data, dict):
            raise bad_request("Body must be a JSON object")
        return data


@dataclass
class InboundMessage:
    user_id: str  # stable id, e.g. "whatsapp:+919800000001"
    text: str = ""
    media_url: str | None = None
    media_content_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    message_id: str | None = None


@dataclass
class VoiceResponse:
    pickup_id: str
    digit: str


class MessagingProvider(ABC):
    name: str = "base"

    @abstractmethod
    def verify_request(self, request: WebhookRequest) -> bool:
        """True if the inbound webhook really comes from the provider."""

    @abstractmethod
    def parse_inbound(self, request: WebhookRequest) -> InboundMessage: ...

    @abstractmethod
    def send_text(self, to: str, text: str) -> str:
        """Send a message; returns the provider message id. Raises ProviderError."""

    @abstractmethod
    def address_for(self, identifier: str) -> str:
        """Turn a user_id or bare phone number into this provider's recipient address."""

    def fetch_media(self, url: str) -> tuple[bytes, str] | None:
        """Download inbound media -> (bytes, content_type). None = use the URL as-is."""
        return None

    def delete_media(self, url: str) -> None:
        """Remove the provider's own copy of inbound media once we stored it privately."""

    def webhook_ack(self) -> tuple[str, str]:
        """(content_type, body) returned to the provider after handling a webhook."""
        return "application/json", json.dumps({"ok": True})


class VoiceProvider(ABC):
    name: str = "base"

    @abstractmethod
    def place_agent_call(self, to_phone: str, agent_url: str, status_callback_url: str) -> str:
        """Call a user and connect them to the conversational voice agent."""

    @abstractmethod
    def place_offer_call(self, to_phone: str, offer: dict[str, Any], callback_url: str) -> str:
        """Phone the recycler and read out `offer`. `offer` never has the address.

        The recycler presses 1/2; the provider must then call `callback_url`
        (handled by POST /voice/response). Returns the provider call id.
        """

    @abstractmethod
    def verify_request(self, request: WebhookRequest) -> bool: ...

    @abstractmethod
    def parse_response(self, request: WebhookRequest) -> VoiceResponse: ...

    def ack(self, result: dict[str, Any]) -> tuple[str, str]:
        """(content_type, body) answering the /voice/response callback."""
        return "application/json", json.dumps(result)


def invalid_signature(request: "WebhookRequest | None" = None) -> ApiError:
    """403 for a webhook that failed verification. The URL used for the check is logged (it is
    not secret) so a URL mismatch can be told apart from a wrong auth token."""
    if request is not None:
        from ecolink.core.log import log

        log("signature_check_failed", url=request.url, signature_header_present=bool(
            request.headers.get("x-twilio-signature")), body_params=len(request.form_pairs()))
    return ApiError(403, "invalid_signature", "Webhook signature verification failed")
