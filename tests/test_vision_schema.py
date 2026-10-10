"""
Unit tests for ml.vision.schemas — pure Python, no GPU/model weights required.
"""
import os
import sys

# Ensure repo root is on the path so `ml` package resolves
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ml.vision.schemas import (
    BoundingBox,
    ClassesConfig,
    ClassifyRequest,
    ClassifyResponse,
    ConfidenceLevel,
    Detection,
    confidence_tier,
    detection_to_public,
    detections_to_response,
    load_classes_config,
)

# ── fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def cfg() -> ClassesConfig:
    return load_classes_config()


@pytest.fixture
def sample_detection() -> Detection:
    return Detection(
        label="plastic",
        class_id=2,
        confidence=0.88,
        bbox=BoundingBox(x1=10.0, y1=20.0, x2=100.0, y2=150.0),
        confidence_level=ConfidenceLevel.HIGH,
        hazardous=False,
        requires_confirmation=False,
    )


# ── ClassesConfig tests ───────────────────────────────────────────────────────

def test_class_names_order(cfg: ClassesConfig) -> None:
    expected = ["paper", "cardboard", "plastic", "metal", "glass", "ewaste", "battery"]
    assert cfg.class_names == expected


def test_id_for_label(cfg: ClassesConfig) -> None:
    assert cfg.id_for("paper") == 0
    assert cfg.id_for("cardboard") == 1
    assert cfg.id_for("plastic") == 2
    assert cfg.id_for("metal") == 3
    assert cfg.id_for("glass") == 4
    assert cfg.id_for("ewaste") == 5
    assert cfg.id_for("battery") == 6


def test_label_for_id(cfg: ClassesConfig) -> None:
    assert cfg.label_for(0) == "paper"
    assert cfg.label_for(1) == "cardboard"
    assert cfg.label_for(2) == "plastic"
    assert cfg.label_for(3) == "metal"
    assert cfg.label_for(4) == "glass"
    assert cfg.label_for(5) == "ewaste"
    assert cfg.label_for(6) == "battery"


def test_unknown_label_raises(cfg: ClassesConfig) -> None:
    with pytest.raises(KeyError):
        cfg.id_for("gold")


def test_out_of_range_id_raises(cfg: ClassesConfig) -> None:
    with pytest.raises(IndexError):
        cfg.label_for(99)


def test_hazardous_set(cfg: ClassesConfig) -> None:
    assert cfg.is_hazardous("ewaste") is True
    assert cfg.is_hazardous("battery") is True
    assert cfg.is_hazardous("paper") is False
    assert cfg.is_hazardous("plastic") is False
    assert cfg.is_hazardous("metal") is False
    assert cfg.is_hazardous("glass") is False
    assert cfg.is_hazardous("cardboard") is False


# ── confidence_tier tests ─────────────────────────────────────────────────────

def test_confidence_tier_high(cfg: ClassesConfig) -> None:
    assert confidence_tier(0.90, cfg) == "HIGH"


def test_confidence_tier_medium(cfg: ClassesConfig) -> None:
    assert confidence_tier(0.65, cfg) == "MEDIUM"


def test_confidence_tier_discard(cfg: ClassesConfig) -> None:
    assert confidence_tier(0.40, cfg) == "DISCARD"


def test_confidence_tier_boundary_high(cfg: ClassesConfig) -> None:
    assert confidence_tier(0.80, cfg) == "HIGH"


def test_confidence_tier_boundary_medium(cfg: ClassesConfig) -> None:
    assert confidence_tier(0.50, cfg) == "MEDIUM"


# ── BoundingBox tests ─────────────────────────────────────────────────────────

def test_bounding_box_fields() -> None:
    bbox = BoundingBox(x1=1.0, y1=2.0, x2=3.0, y2=4.0)
    assert bbox.x1 == 1.0
    assert bbox.y1 == 2.0
    assert bbox.x2 == 3.0
    assert bbox.y2 == 4.0


# ── ConfidenceLevel enum tests ────────────────────────────────────────────────

def test_confidence_level_enum_values() -> None:
    assert ConfidenceLevel.HIGH == "high"
    assert ConfidenceLevel.MEDIUM == "medium"


# ── Detection dataclass tests ─────────────────────────────────────────────────

def test_detection_construction(sample_detection: Detection) -> None:
    assert sample_detection.label == "plastic"
    assert sample_detection.class_id == 2
    assert sample_detection.confidence == 0.88
    assert sample_detection.hazardous is False
    assert isinstance(sample_detection.bbox, BoundingBox)


def test_detection_label_in_class_names(
    cfg: ClassesConfig, sample_detection: Detection
) -> None:
    assert sample_detection.label in cfg.class_names


# ── detection_to_public tests ─────────────────────────────────────────────────

def test_detection_to_public_keys(sample_detection: Detection) -> None:
    pub = detection_to_public(sample_detection)
    assert set(pub.keys()) == {"label", "confidence", "hazardous"}


def test_detection_to_public_types(sample_detection: Detection) -> None:
    pub = detection_to_public(sample_detection)
    assert isinstance(pub["label"], str)
    assert isinstance(pub["confidence"], float)
    assert isinstance(pub["hazardous"], bool)


def test_detection_to_public_confidence_rounded(sample_detection: Detection) -> None:
    det = Detection(
        label="plastic",
        class_id=2,
        confidence=0.8567,
        bbox=BoundingBox(0, 0, 1, 1),
        confidence_level=ConfidenceLevel.HIGH,
        hazardous=False,
        requires_confirmation=False,
    )
    pub = detection_to_public(det)
    assert pub["confidence"] == 0.86


def test_detection_to_public_hazardous_battery() -> None:
    det = Detection(
        label="battery",
        class_id=6,
        confidence=0.91,
        bbox=BoundingBox(0, 0, 1, 1),
        confidence_level=ConfidenceLevel.HIGH,
        hazardous=True,
        requires_confirmation=False,
    )
    pub = detection_to_public(det)
    assert pub["hazardous"] is True
    assert pub["label"] == "battery"


# ── detections_to_response tests ─────────────────────────────────────────────

def test_detections_to_response_shape(sample_detection: Detection) -> None:
    result = detections_to_response([sample_detection])
    assert "items" in result
    assert len(result["items"]) == 1


def test_detections_to_response_empty() -> None:
    result = detections_to_response([])
    assert result == {"items": []}


# ── ClassifyResponse tests ────────────────────────────────────────────────────

def test_classify_response_to_api_dict(sample_detection: Detection) -> None:
    pub = detection_to_public(sample_detection)
    resp = ClassifyResponse(items=[pub])
    api = resp.to_api_dict()
    assert "items" in api
    assert len(api["items"]) == 1
    item = api["items"][0]
    assert "label" in item
    assert "confidence" in item
    assert "hazardous" in item


# ── ClassifyRequest tests ─────────────────────────────────────────────────────

def test_classify_request_has_image_url() -> None:
    req = ClassifyRequest(image_url="https://bucket.s3.amazonaws.com/img.jpg")
    assert req.image_url == "https://bucket.s3.amazonaws.com/img.jpg"
