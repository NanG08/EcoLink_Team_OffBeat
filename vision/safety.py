"""
Safety, confidence logic, and URL validation for the EcoLink vision pipeline.

Loads class config from ml/vision/config/classes.yaml.
Loads thresholds from ml/vision/config/inference.yaml.

Does NOT import from ecolink.* — fully standalone.
"""
from __future__ import annotations

import ipaddress
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import yaml

from ml.vision.schemas import (
    BoundingBox,
    ClassesConfig,
    ConfidenceLevel,
    Detection,
    load_classes_config,
)

# ─────────────────────────────────────────────────────────────────────────────
# Config loading
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).parent / "config"


def _load_inference_config() -> dict:
    with open(_CONFIG_DIR / "inference.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


_classes_cfg: ClassesConfig = load_classes_config()
_inf_cfg: dict = _load_inference_config()

CLASS_NAMES: list[str] = _classes_cfg.class_names
CLASS_IDS: dict[str, int] = {name: _classes_cfg.id_for(name) for name in CLASS_NAMES}
HAZARDOUS_CLASSES: frozenset[str] = _classes_cfg.hazardous_labels

DETECTION_THRESHOLD: float = _inf_cfg["confidence"]["detection_threshold"]
HIGH_CONFIDENCE_THRESHOLD: float = _inf_cfg["confidence"]["high_confidence_threshold"]
HAZARDOUS_CONFIRMATION_THRESHOLD: float = _inf_cfg["confidence"]["hazardous_confirmation_threshold"]

# ─────────────────────────────────────────────────────────────────────────────
# URL / host validation (SSRF prevention)
# ─────────────────────────────────────────────────────────────────────────────

# Private / link-local CIDRs that must never be reached
_PRIVATE_CIDRS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]


def _is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* resolves to (or IS) a private/link-local address."""
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in cidr for cidr in _PRIVATE_CIDRS)
    except ValueError:
        # Not a bare IP; check well-known private hostnames
        h = hostname.lower()
        return h in {"localhost", "ip6-localhost", "ip6-loopback"}


def is_allowed_host(hostname: str, extra_hosts: frozenset[str] = frozenset()) -> bool:
    """
    Return True only for hostnames ending in ``.amazonaws.com`` or present in *extra_hosts*.
    """
    hostname = hostname.lower()
    if hostname in extra_hosts:
        return True
    return hostname.endswith(".amazonaws.com") or hostname == "amazonaws.com"


def validate_image_url(url: str, extra_hosts: frozenset[str] = frozenset()) -> None:
    """
    Raise ValueError with a descriptive message if the URL fails any security check:

    - scheme must be ``https``
    - hostname must pass :func:`is_allowed_host`
    - hostname must not be a private / link-local address
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(
            f"image_url must use HTTPS (got scheme '{parsed.scheme}')"
        )
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("image_url has no hostname")
    if _is_private_ip(hostname):
        raise ValueError(
            f"image_url hostname '{hostname}' resolves to a private/link-local address"
        )
    if not is_allowed_host(hostname, extra_hosts):
        raise ValueError(
            f"image_url host '{hostname}' is not allowed; use a pre-signed S3 URL "
            f"(*.amazonaws.com)"
        )


def fetch_image_bytes(
    url: str,
    max_bytes: int = 5 * 1024 * 1024,
    timeout: int = 10,
    extra_hosts: frozenset[str] = frozenset(),
) -> bytes:
    """
    Fetch image bytes from *url* after calling :func:`validate_image_url`.

    Raises:
        ValueError: URL validation failure or download exceeds *max_bytes*.
        urllib.error.URLError: Network error.
        urllib.error.HTTPError: Non-2xx HTTP response.
    """
    validate_image_url(url, extra_hosts)
    req = urllib.request.Request(url, headers={"User-Agent": "EcoLink/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(
            f"Image download exceeds {max_bytes} byte limit ({len(data)} bytes received)"
        )
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Confidence / hazardous logic
# ─────────────────────────────────────────────────────────────────────────────

def get_confidence_level(confidence: float) -> ConfidenceLevel:
    """
    Return :class:`ConfidenceLevel` for *confidence*.

    Raises ValueError if confidence is below the detection threshold.
    """
    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return ConfidenceLevel.HIGH
    if confidence >= DETECTION_THRESHOLD:
        return ConfidenceLevel.MEDIUM
    raise ValueError(
        f"Confidence {confidence} is below detection threshold {DETECTION_THRESHOLD}"
    )


def is_hazardous(label: str) -> bool:
    """Return True if *label* is a hazardous waste class."""
    return label in HAZARDOUS_CLASSES


def requires_confirmation(label: str, confidence: float) -> bool:
    """
    Return True if this detection should trigger a user confirmation prompt.

    Rules:
    - Hazardous classes below HAZARDOUS_CONFIRMATION_THRESHOLD → True
    - Non-hazardous classes below HIGH_CONFIDENCE_THRESHOLD → True
    """
    if is_hazardous(label):
        return confidence < HAZARDOUS_CONFIRMATION_THRESHOLD
    return confidence < HIGH_CONFIDENCE_THRESHOLD


def apply_safety_rules(detections: list[Detection]) -> list[Detection]:
    """
    Filter detections below the detection threshold and recompute derived fields.

    Returns a new list (input is not mutated).
    """
    filtered = [d for d in detections if d.confidence >= DETECTION_THRESHOLD]
    result: list[Detection] = []
    for det in filtered:
        result.append(
            Detection(
                label=det.label,
                class_id=det.class_id,
                confidence=det.confidence,
                bbox=det.bbox,
                confidence_level=get_confidence_level(det.confidence),
                hazardous=is_hazardous(det.label),
                requires_confirmation=requires_confirmation(det.label, det.confidence),
            )
        )
    return result


def build_detection(
    label: str,
    class_id: int,
    confidence: float,
    bbox_xyxy: tuple,
) -> Detection:
    """
    Construct a :class:`Detection` from raw YOLO output fields.

    *bbox_xyxy* must be ``(x1, y1, x2, y2)`` in pixel coordinates.

    Detections below the detection threshold receive ConfidenceLevel.MEDIUM
    as a placeholder; :func:`apply_safety_rules` will filter them out.
    """
    x1, y1, x2, y2 = bbox_xyxy
    bbox = BoundingBox(x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2))
    conf = float(confidence)
    # Use a safe confidence level — below-threshold detections will be
    # removed by apply_safety_rules; we avoid raising here.
    try:
        conf_level = get_confidence_level(conf)
    except ValueError:
        conf_level = ConfidenceLevel.MEDIUM  # placeholder; filtered by apply_safety_rules
    return Detection(
        label=label,
        class_id=class_id,
        confidence=conf,
        bbox=bbox,
        confidence_level=conf_level,
        hazardous=is_hazardous(label),
        requires_confirmation=requires_confirmation(label, conf),
    )
