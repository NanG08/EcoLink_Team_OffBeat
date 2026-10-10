"""
Unit tests for ml.vision.safety — no network access, no GPU, stdlib only.
"""
import io
import os
import sys
import urllib.error
import unittest.mock as mock

# Ensure repo root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ml.vision.safety import (
    CLASS_NAMES,
    DETECTION_THRESHOLD,
    HAZARDOUS_CLASSES,
    HIGH_CONFIDENCE_THRESHOLD,
    HAZARDOUS_CONFIRMATION_THRESHOLD,
    apply_safety_rules,
    build_detection,
    fetch_image_bytes,
    get_confidence_level,
    is_allowed_host,
    is_hazardous,
    requires_confirmation,
    validate_image_url,
)
from ml.vision.schemas import ConfidenceLevel, BoundingBox, Detection


# ── is_allowed_host ───────────────────────────────────────────────────────────

def test_allowed_amazonaws_subdomain() -> None:
    assert is_allowed_host("bucket.s3.amazonaws.com") is True


def test_allowed_amazonaws_root() -> None:
    assert is_allowed_host("s3.amazonaws.com") is True


def test_allowed_deep_amazonaws_subdomain() -> None:
    assert is_allowed_host("mybucket.s3.us-east-1.amazonaws.com") is True


def test_blocked_non_aws() -> None:
    assert is_allowed_host("evil.example.com") is False


def test_blocked_google() -> None:
    assert is_allowed_host("storage.googleapis.com") is False


def test_extra_hosts_allow_list() -> None:
    assert is_allowed_host("cdn.myapp.com", extra_hosts=frozenset({"cdn.myapp.com"})) is True
    assert is_allowed_host("other.com", extra_hosts=frozenset({"cdn.myapp.com"})) is False


# ── validate_image_url ────────────────────────────────────────────────────────

def test_valid_presigned_url_passes() -> None:
    validate_image_url("https://mybucket.s3.amazonaws.com/images/foo.jpg")


def test_blocked_http_scheme() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        validate_image_url("http://bucket.s3.amazonaws.com/x.jpg")


def test_blocked_localhost() -> None:
    with pytest.raises(ValueError):
        validate_image_url("https://localhost/x.jpg")


def test_blocked_link_local() -> None:
    with pytest.raises(ValueError):
        validate_image_url("https://169.254.169.254/latest/meta-data")


def test_blocked_private_10() -> None:
    with pytest.raises(ValueError):
        validate_image_url("https://10.0.0.1/x.jpg")


def test_blocked_private_172() -> None:
    with pytest.raises(ValueError):
        validate_image_url("https://172.16.0.1/x.jpg")


def test_blocked_private_192_168() -> None:
    with pytest.raises(ValueError):
        validate_image_url("https://192.168.1.1/x.jpg")


def test_blocked_non_amazonaws_host() -> None:
    with pytest.raises(ValueError):
        validate_image_url("https://evil.attacker.com/img.jpg")


def test_blocked_no_scheme() -> None:
    with pytest.raises(ValueError):
        validate_image_url("bucket.s3.amazonaws.com/x.jpg")


# ── fetch_image_bytes ─────────────────────────────────────────────────────────

def _make_mock_response(data: bytes, content_type: str = "image/jpeg"):
    """Build a mock urllib response context manager."""
    resp = mock.MagicMock()
    resp.read.return_value = data
    resp.headers = {"Content-Type": content_type}
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    return resp


@mock.patch("ml.vision.safety.urllib.request.urlopen")
def test_fetch_caps_at_max_bytes(mock_urlopen: mock.MagicMock) -> None:
    """fetch_image_bytes raises ValueError when download exceeds max_bytes."""
    oversized_data = b"X" * (5 * 1024 * 1024 + 1)
    mock_urlopen.return_value = _make_mock_response(oversized_data)
    with pytest.raises(ValueError, match="limit|exceed"):
        fetch_image_bytes(
            "https://bucket.s3.amazonaws.com/big.jpg",
            max_bytes=5 * 1024 * 1024,
        )


@mock.patch("ml.vision.safety.urllib.request.urlopen")
def test_fetch_succeeds_within_limit(mock_urlopen: mock.MagicMock) -> None:
    small_data = b"\xff\xd8\xff" + b"X" * 100   # fake JPEG header
    mock_urlopen.return_value = _make_mock_response(small_data)
    result = fetch_image_bytes(
        "https://bucket.s3.amazonaws.com/img.jpg",
        max_bytes=5 * 1024 * 1024,
    )
    assert result == small_data


# ── hazardous detection ───────────────────────────────────────────────────────

def test_battery_is_hazardous() -> None:
    assert is_hazardous("battery") is True


def test_ewaste_is_hazardous() -> None:
    assert is_hazardous("ewaste") is True


def test_paper_not_hazardous() -> None:
    assert is_hazardous("paper") is False


def test_plastic_not_hazardous() -> None:
    assert is_hazardous("plastic") is False


def test_glass_not_hazardous() -> None:
    assert is_hazardous("glass") is False


def test_metal_not_hazardous() -> None:
    assert is_hazardous("metal") is False


def test_cardboard_not_hazardous() -> None:
    assert is_hazardous("cardboard") is False


# ── requires_confirmation ─────────────────────────────────────────────────────

def test_battery_low_confidence_requires_confirmation() -> None:
    # 0.60 < HAZARDOUS_CONFIRMATION_THRESHOLD (0.70)
    assert requires_confirmation("battery", 0.60) is True


def test_battery_high_confidence_no_confirmation() -> None:
    # 0.97 >= HAZARDOUS_CONFIRMATION_THRESHOLD (0.70)
    assert requires_confirmation("battery", 0.97) is False


def test_plastic_medium_confidence_requires_confirmation() -> None:
    # 0.75 < HIGH_CONFIDENCE_THRESHOLD (0.80)
    assert requires_confirmation("plastic", 0.75) is True


def test_plastic_high_confidence_no_confirmation() -> None:
    # 0.90 >= HIGH_CONFIDENCE_THRESHOLD (0.80)
    assert requires_confirmation("plastic", 0.90) is False


# ── get_confidence_level ──────────────────────────────────────────────────────

def test_get_confidence_level_high() -> None:
    assert get_confidence_level(0.90) == ConfidenceLevel.HIGH


def test_get_confidence_level_medium() -> None:
    assert get_confidence_level(0.60) == ConfidenceLevel.MEDIUM


def test_get_confidence_level_raises_below_threshold() -> None:
    with pytest.raises(ValueError):
        get_confidence_level(0.30)


def test_get_confidence_level_high_boundary() -> None:
    assert get_confidence_level(0.80) == ConfidenceLevel.HIGH


def test_get_confidence_level_medium_boundary() -> None:
    assert get_confidence_level(0.50) == ConfidenceLevel.MEDIUM


# ── apply_safety_rules ────────────────────────────────────────────────────────

def _make_det(label: str, class_id: int, confidence: float) -> Detection:
    return Detection(
        label=label,
        class_id=class_id,
        confidence=confidence,
        bbox=BoundingBox(0, 0, 100, 100),
        confidence_level=ConfidenceLevel.HIGH if confidence >= 0.80 else ConfidenceLevel.MEDIUM,
        hazardous=is_hazardous(label),
        requires_confirmation=requires_confirmation(label, confidence),
    )


def test_apply_safety_rules_filters_below_threshold() -> None:
    dets = [
        _make_det("plastic", 2, 0.30),   # below threshold
        _make_det("paper", 0, 0.70),     # above threshold
    ]
    result = apply_safety_rules(dets)
    assert len(result) == 1
    assert result[0].label == "paper"


def test_apply_safety_rules_preserves_above_threshold() -> None:
    dets = [
        _make_det("plastic", 2, 0.50),   # exactly at threshold
        _make_det("paper", 0, 0.85),
        _make_det("battery", 6, 0.92),
    ]
    result = apply_safety_rules(dets)
    assert len(result) == 3


def test_apply_safety_rules_recomputes_hazardous() -> None:
    det = _make_det("battery", 6, 0.80)
    result = apply_safety_rules([det])
    assert result[0].hazardous is True


# ── CLASS_NAMES constant ──────────────────────────────────────────────────────

def test_class_names_content() -> None:
    expected = ["paper", "cardboard", "plastic", "metal", "glass", "ewaste", "battery"]
    assert CLASS_NAMES == expected


def test_hazardous_classes_set() -> None:
    assert "ewaste" in HAZARDOUS_CLASSES
    assert "battery" in HAZARDOUS_CLASSES
    assert "paper" not in HAZARDOUS_CLASSES
