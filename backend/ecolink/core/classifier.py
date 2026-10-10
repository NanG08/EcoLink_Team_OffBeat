"""POST /classify implementation: a vision model on Amazon Bedrock (Converse API).

Environment:
  CLASSIFY_MODEL_ID        Bedrock model / inference-profile id (must accept images,
                           e.g. apac.amazon.nova-pro-v1:0 when running in ap-south-1)

The image URL is caller-supplied, so it is fetched only over https and only from PUBLIC
addresses: every IP the host resolves to must be globally routable (blocks loopback,
private ranges and the 169.254.169.254 metadata endpoint), redirects are re-checked at
each hop, and the download is capped at 5 MB. Pre-signed S3 URLs and any public image URL
therefore work.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin, urlparse

from ecolink.core.constants import HAZARDOUS_LABELS, LABELS
from ecolink.core.http import ApiError, bad_request
from ecolink.core.storage import MAX_IMAGE_BYTES

_PROMPT = (
    "You are a waste-sorting assistant for Indian households. Look at the photo and list the "
    "distinct waste items you can see. Use ONLY these labels: "
    + ", ".join(LABELS)
    + ". Use 'ewaste' for electronic devices, cables and chargers, and 'battery' for any "
    "batteries or power banks. Reply with JSON only, no prose, exactly in the form "
    '{"items":[{"label":"plastic","confidence":0.9}]}. confidence is between 0 and 1. '
    'If you see no waste items reply {"items":[]}.'
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


MAX_REDIRECTS = 3


def assert_public_https(url: str) -> None:
    """Raise 400 unless `url` is https and every address its host resolves to is public."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise bad_request("'image_url' must be an https URL")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise bad_request("'image_url' host could not be resolved") from None
    for info in infos:
        address = info[4][0].split("%")[0]  # drop IPv6 zone id
        if not ipaddress.ip_address(address).is_global:
            raise bad_request("'image_url' must point to a public host")


def fetch_image(url: str) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(MAX_REDIRECTS + 1):
        assert_public_https(url)
        try:
            with opener.open(url, timeout=10) as resp:
                data = resp.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.code in (301, 302, 303, 307, 308) else None
            if location:
                url = urljoin(url, location)  # validated again on the next loop
                continue
            raise bad_request(f"Could not download the image (HTTP {exc.code}); the link may have expired") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(502, "image_fetch_failed", f"Could not download the image: {exc}") from exc
        if len(data) > MAX_IMAGE_BYTES:
            raise bad_request("Image is larger than 5 MB")
        return data
    raise bad_request("Too many redirects while downloading the image")


def image_format(data: bytes) -> str:
    """Bedrock wants a format name; sniff it from the magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    raise bad_request("Unsupported image type; send a JPEG, PNG or WebP photo")


def parse_model_output(text: str) -> list[dict[str, Any]]:
    """Extract {"items": [...]} from the model's reply and normalise it to the contract."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ApiError(502, "classifier_error", "Classifier did not return JSON")
    try:
        raw_items = json.loads(match.group(0)).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        raise ApiError(502, "classifier_error", "Classifier returned malformed JSON") from None
    best: dict[str, float] = {}
    for entry in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip().lower()
        if label not in LABELS:
            continue
        try:
            confidence = min(1.0, max(0.0, float(entry.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        best[label] = max(best.get(label, 0.0), confidence)
    return [
        {"label": label, "confidence": round(conf, 2), "hazardous": label in HAZARDOUS_LABELS}
        for label, conf in sorted(best.items(), key=lambda kv: -kv[1])
    ]


def classify(image_url: str, client: Any | None = None) -> dict[str, Any]:
    model_id = os.environ.get("CLASSIFY_MODEL_ID")
    if not model_id:
        raise ApiError(503, "not_configured", "CLASSIFY_MODEL_ID is not set")
    data = fetch_image(image_url)
    fmt = image_format(data)
    if client is None:
        import boto3

        client = boto3.client("bedrock-runtime")
    try:
        resp = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [
                {"image": {"format": fmt, "source": {"bytes": data}}},
                {"text": _PROMPT},
            ]}],
            inferenceConfig={"maxTokens": 500, "temperature": 0},
        )
        text = "".join(
            block.get("text", "") for block in resp["output"]["message"]["content"]
        )
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - botocore ClientError etc.
        raise ApiError(502, "classifier_error", f"Bedrock call failed: {exc}") from exc
    return {"items": parse_model_output(text)}
