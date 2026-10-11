"""Twilio adapter (WhatsApp sandbox/number + programmable voice) using only the stdlib.

Credentials come from environment variables or, in AWS, a Secrets Manager secret
(TWILIO_SECRET_NAME) holding JSON:
    {"account_sid": "AC...", "auth_token": "...",
     "whatsapp_from": "whatsapp:+14155238886", "voice_from": "+1..."}
Env vars TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM /
TWILIO_VOICE_FROM take precedence (local development). Nothing is stored in the repo.
TWILIO_API_BASE overrides the REST base URL (default https://api.twilio.com/2010-04-01).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from ecolink.core.http import bad_request
from ecolink.core.messages import describe_items
from ecolink.core.secrets import get_secret_json
from ecolink.providers.base import (
    InboundMessage,
    MessagingProvider,
    ProviderError,
    VoiceProvider,
    VoiceResponse,
    WebhookRequest,
)

_DEFAULT_API = "https://api.twilio.com/2010-04-01"
_MAX_MEDIA_BYTES = 5 * 1024 * 1024
_ENV_KEYS = {
    "account_sid": "TWILIO_ACCOUNT_SID",
    "auth_token": "TWILIO_AUTH_TOKEN",
    "whatsapp_from": "TWILIO_WHATSAPP_FROM",
    "voice_from": "TWILIO_VOICE_FROM",
}


def _credentials() -> dict[str, str]:
    creds = {k: os.environ[v] for k, v in _ENV_KEYS.items() if os.environ.get(v)}
    if len(creds) < len(_ENV_KEYS):
        secret_name = os.environ.get("TWILIO_SECRET_NAME")
        if secret_name:
            creds = {**get_secret_json(secret_name), **creds}
    missing = [k for k in _ENV_KEYS if not creds.get(k)]
    if missing:
        raise ProviderError(f"Twilio credentials missing: {', '.join(missing)}")
    return creds


def compute_signature(auth_token: str, url: str, form_pairs: list[tuple[str, str]]) -> str:
    """Twilio's X-Twilio-Signature: base64(HMAC-SHA1(token, url + sorted(key+value)))."""
    data = url + "".join(k + v for k, v in sorted(form_pairs))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _signature_variants(request: WebhookRequest) -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """Alternative (url, params) readings of one request, used ONLY to diagnose a failed check."""
    pairs = request.form_pairs()
    parsed = urllib.parse.urlparse(request.url)
    raw_pairs = [tuple(kv.split("=", 1)) if "=" in kv else (kv, "") for kv in request.body.split("&") if kv]
    return {
        "url_with_port_443": (parsed._replace(netloc=f"{parsed.netloc}:443").geturl(), pairs),
        "plus_kept_as_plus": (request.url, [(urllib.parse.unquote(k), urllib.parse.unquote(v)) for k, v in raw_pairs]),
        "blank_params_dropped": (request.url, [(k, v) for k, v in pairs if v != ""]),
        "http_scheme": (request.url.replace("https://", "http://", 1), pairs),
        "no_params": (request.url, []),
    }


def _verify(request: WebhookRequest) -> bool:
    provided = request.headers.get("x-twilio-signature", "")
    token = _credentials()["auth_token"]
    expected = compute_signature(token, request.url, request.form_pairs())
    if provided and hmac.compare_digest(provided, expected):
        return True
    if provided:  # diagnosis only: report which alternative reading WOULD have matched (never accepted)
        from ecolink.core.log import log

        matching = [name for name, (url, params) in _signature_variants(request).items()
                    if hmac.compare_digest(provided, compute_signature(token, url, params))]
        log("signature_variants_matching", matching=matching,
            token_length=len(token), param_names=sorted(k for k, _ in request.form_pairs()))
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def _is_twilio_media_host(url: str) -> bool:
    """Credentials are only ever sent to Twilio (or the operator-configured TWILIO_API_BASE host)."""
    host = urllib.parse.urlparse(url).hostname or ""
    configured = urllib.parse.urlparse(os.environ.get("TWILIO_API_BASE", "")).hostname
    return host.endswith(".twilio.com") or (bool(configured) and host == configured)


def _basic_auth(creds: dict[str, str]) -> str:
    token = base64.b64encode(f"{creds['account_sid']}:{creds['auth_token']}".encode()).decode()
    return f"Basic {token}"


def _post_form(path: str, fields: dict[str, str], creds: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{os.environ.get('TWILIO_API_BASE', _DEFAULT_API).rstrip('/')}/Accounts/{creds['account_sid']}/{path}",
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Authorization": _basic_auth(creds)},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed https host
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise ProviderError(f"Twilio {path} rejected the request ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderError(f"Twilio {path} unreachable: {exc}") from exc


class TwilioMessagingProvider(MessagingProvider):
    name = "twilio"

    def verify_request(self, request: WebhookRequest) -> bool:
        return _verify(request)

    def parse_inbound(self, request: WebhookRequest) -> InboundMessage:
        f = request.form()
        sender = f.get("From", "").strip()
        if not sender:
            raise bad_request("Missing 'From' in webhook payload")

        def _float(key: str) -> float | None:
            try:
                return float(f[key])
            except (KeyError, ValueError):
                return None

        has_media = f.get("NumMedia", "0") not in ("", "0")
        return InboundMessage(
            user_id=sender,
            text=f.get("Body", "").strip(),
            media_url=f.get("MediaUrl0") if has_media else None,
            media_content_type=f.get("MediaContentType0") if has_media else None,
            latitude=_float("Latitude"),
            longitude=_float("Longitude"),
            message_id=f.get("MessageSid"),
        )

    def address_for(self, identifier: str) -> str:
        return identifier if ":" in identifier else f"whatsapp:{identifier}"

    def send_text(self, to: str, text: str) -> str:
        creds = _credentials()
        body = _post_form(
            "Messages.json",
            {"From": creds["whatsapp_from"], "To": self.address_for(to), "Body": text[:1500]},
            creds,
        )
        return str(body.get("sid", ""))

    def fetch_media(self, url: str) -> tuple[bytes, str] | None:
        if not _is_twilio_media_host(url):  # never send credentials to other hosts
            raise ProviderError("Refusing to fetch media from a non-Twilio host")
        creds = _credentials()
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(url, headers={"Authorization": _basic_auth(creds)})
        try:
            try:
                resp = opener.open(req, timeout=10)
            except urllib.error.HTTPError as exc:
                # Twilio redirects to a pre-signed storage URL; follow it WITHOUT credentials.
                location = exc.headers.get("Location") if exc.code in (301, 302, 303, 307) else None
                if not location:
                    raise
                resp = urllib.request.urlopen(location, timeout=10)  # noqa: S310
            with resp:
                data = resp.read(_MAX_MEDIA_BYTES + 1)
                content_type = resp.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"Could not download media: {exc}") from exc
        if len(data) > _MAX_MEDIA_BYTES:
            raise ProviderError("Media is larger than 5 MB")
        return data, content_type

    def delete_media(self, url: str) -> None:
        """DELETE the media resource so photos do not linger on Twilio after we copied them."""
        if not _is_twilio_media_host(url):
            raise ProviderError("Refusing to delete media on a non-Twilio host")
        req = urllib.request.Request(
            url + ".json", headers={"Authorization": _basic_auth(_credentials())}, method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=10).close()  # noqa: S310
        except urllib.error.HTTPError as exc:
            if exc.code != 404:  # already gone is fine
                raise ProviderError(f"Could not delete media ({exc.code})") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"Could not delete media: {exc}") from exc

    def webhook_ack(self) -> tuple[str, str]:
        return "text/xml", "<Response/>"


def _spoken_slot(slot: str | None) -> str:
    if not slot:
        return "not specified"
    try:
        dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        return dt.strftime("%d %B at %I %p").lstrip("0")
    except ValueError:
        return slot


def build_offer_twiml(offer: dict[str, Any], action_url: str) -> str:
    """TwiML read to the recycler. Built from the offer payload only -- no address."""
    parts = [f"Hello from EcoLink. New scrap pickup request: {describe_items(offer.get('items', []))}."]
    if offer.get("distance_km") is not None:
        parts.append(f"It is about {float(offer['distance_km']):g} kilometres from you.")
    parts.append(f"Preferred slot: {_spoken_slot(offer.get('slot'))}.")
    parts.append("Press 1 to accept, or 2 to decline.")
    message = escape(" ".join(parts))
    action = escape(action_url, {'"': "&quot;"})
    return (
        "<Response>"
        f'<Gather numDigits="1" timeout="10" method="POST" action="{action}">'
        f'<Say language="en-IN">{message}</Say>'
        "</Gather>"
        '<Say language="en-IN">We did not receive a response. Goodbye.</Say>'
        "</Response>"
    )


class TwilioVoiceProvider(VoiceProvider):
    name = "twilio"

    def place_agent_call(self, to_phone: str, agent_url: str, status_callback_url: str) -> str:
        creds = _credentials()
        body = _post_form(
            "Calls.json",
            {
                "To": to_phone,
                "From": creds["voice_from"],
                "Url": agent_url,
                "Method": "POST",
                "StatusCallback": status_callback_url,
                "StatusCallbackMethod": "POST",
                "StatusCallbackEvent": "completed",
                "Timeout": "30",
            },
            creds,
        )
        return str(body.get("sid", ""))

    def place_offer_call(self, to_phone: str, offer: dict[str, Any], callback_url: str) -> str:
        creds = _credentials()
        body = _post_form(
            "Calls.json",
            {
                "To": to_phone,
                "From": creds["voice_from"],
                "Twiml": build_offer_twiml(offer, callback_url),
                "Timeout": "30",
            },
            creds,
        )
        return str(body.get("sid", ""))

    def verify_request(self, request: WebhookRequest) -> bool:
        return _verify(request)

    def parse_response(self, request: WebhookRequest) -> VoiceResponse:
        # Twilio posts the keypad press as form field `Digits`; our call TwiML puts pickup_id
        # in the callback URL's query string. The contract names (pickup_id, digit) also work.
        form = request.form()
        pickup_id = request.query.get("pickup_id") or form.get("pickup_id", "")
        digit = (form.get("Digits") or form.get("digit") or "").strip()
        if not pickup_id or not digit:
            raise bad_request("'pickup_id' and 'Digits' (the pressed key) are required")
        return VoiceResponse(pickup_id=pickup_id, digit=digit)

    def ack(self, result: dict[str, Any]) -> tuple[str, str]:
        if result.get("status") == "confirmed":
            say = "Thank you. Pickup confirmed. The address is on its way to you by message."
        else:
            say = "No problem. Thank you. Goodbye."
        return "text/xml", f'<Response><Say language="en-IN">{escape(say)}</Say></Response>'
