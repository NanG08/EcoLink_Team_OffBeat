"""
Tests for confidence threshold boundary behaviour in the EcoLink vision pipeline.
"""
import io
import os
import sys

# Ensure repo root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from PIL import Image

from ml.vision.schemas import (
    BoundingBox,
    ClassifyResponse,
    ConfidenceLevel,
    Detection,
    detection_to_public,
    load_classes_config,
)
from ml.vision.safety import (
    build_detection,
    get_confidence_level,
    is_hazardous,
    requires_confirmation,
)


# ─────────────────────────────────────────────────────────────────────────────
# ConfidenceLevel boundary tests
# ─────────────────────────────────────────────────────────────────────────────

def test_confidence_090_is_high() -> None:
    assert get_confidence_level(0.90) == ConfidenceLevel.HIGH


def test_confidence_080_is_high_boundary() -> None:
    assert get_confidence_level(0.80) == ConfidenceLevel.HIGH


def test_confidence_079_is_medium() -> None:
    assert get_confidence_level(0.79) == ConfidenceLevel.MEDIUM


def test_confidence_050_is_medium_boundary() -> None:
    assert get_confidence_level(0.50) == ConfidenceLevel.MEDIUM


def test_confidence_049_raises_value_error() -> None:
    with pytest.raises(ValueError):
        get_confidence_level(0.49)


def test_confidence_000_raises_value_error() -> None:
    with pytest.raises(ValueError):
        get_confidence_level(0.00)


# ─────────────────────────────────────────────────────────────────────────────
# Battery hazardous + confirmation tests
# ─────────────────────────────────────────────────────────────────────────────

def test_battery_062_hazardous_requires_confirmation() -> None:
    # 0.62 < HAZARDOUS_CONFIRMATION_THRESHOLD (0.70)
    det = build_detection("battery", 6, 0.62, (0, 0, 100, 100))
    assert det.hazardous is True
    assert det.requires_confirmation is True


def test_battery_097_hazardous_no_confirmation() -> None:
    # 0.97 >= HAZARDOUS_CONFIRMATION_THRESHOLD (0.70)
    det = build_detection("battery", 6, 0.97, (0, 0, 100, 100))
    assert det.hazardous is True
    assert det.requires_confirmation is False


# ─────────────────────────────────────────────────────────────────────────────
# Plastic (non-hazardous) confirmation tests
# ─────────────────────────────────────────────────────────────────────────────

def test_plastic_075_not_hazardous_requires_confirmation() -> None:
    # 0.75 < HIGH_CONFIDENCE_THRESHOLD (0.80)
    det = build_detection("plastic", 2, 0.75, (0, 0, 100, 100))
    assert det.hazardous is False
    assert det.requires_confirmation is True


def test_plastic_095_not_hazardous_no_confirmation() -> None:
    # 0.95 >= HIGH_CONFIDENCE_THRESHOLD (0.80)
    det = build_detection("plastic", 2, 0.95, (0, 0, 100, 100))
    assert det.hazardous is False
    assert det.requires_confirmation is False


# ─────────────────────────────────────────────────────────────────────────────
# build_detection field completeness
# ─────────────────────────────────────────────────────────────────────────────

def test_build_detection_all_fields() -> None:
    det = build_detection("glass", 4, 0.88, (5.0, 10.0, 200.0, 300.0))
    assert det.label == "glass"
    assert det.class_id == 4
    assert det.confidence == 0.88
    assert isinstance(det.bbox, BoundingBox)
    assert det.bbox.x1 == 5.0
    assert det.bbox.y1 == 10.0
    assert det.bbox.x2 == 200.0
    assert det.bbox.y2 == 300.0
    assert det.confidence_level == ConfidenceLevel.HIGH
    assert det.hazardous is False
    assert det.requires_confirmation is False


def test_build_detection_ewaste_hazardous() -> None:
    det = build_detection("ewaste", 5, 0.91, (0, 0, 50, 50))
    assert det.hazardous is True
    assert det.label == "ewaste"


# ─────────────────────────────────────────────────────────────────────────────
# Inference-level confidence tests using FakeModel
# ─────────────────────────────────────────────────────────────────────────────

class FakeBoxes:
    def __init__(self, detections: list[tuple[int, float]]):
        import torch
        self._dets = detections
        self.cls = torch.tensor([float(d[0]) for d in detections])
        self.conf = torch.tensor([float(d[1]) for d in detections])
        self.xyxy = torch.tensor([[10.0, 10.0, 100.0, 100.0] for _ in detections])

    def __len__(self) -> int:
        return len(self._dets)


class FakeResult:
    def __init__(self, detections: list[tuple[int, float]]):
        self.boxes = FakeBoxes(detections)


class FakeModel:
    def __init__(self, detections: list[tuple[int, float]]):
        self._dets = detections

    def __call__(self, image, **kwargs):
        return [FakeResult(self._dets)]


def _jpeg_bytes(w: int = 64, h: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h)).save(buf, format="JPEG")
    return buf.getvalue()


def test_discard_threshold_removes_item() -> None:
    """Detection at 0.40 (below 0.50 threshold) produces zero items."""
    from ml.vision.inference import run_inference

    model = FakeModel([(2, 0.40)])
    result = run_inference(_jpeg_bytes(), model=model)
    assert result.items == []


def test_multiple_items_confidence_ordering() -> None:
    """Three detections → output order is highest confidence first."""
    from ml.vision.inference import run_inference

    model = FakeModel([(0, 0.60), (1, 0.90), (3, 0.75)])
    result = run_inference(_jpeg_bytes(), model=model)
    confs = [item["confidence"] for item in result.items]
    assert confs == sorted(confs, reverse=True)
    assert confs[0] == 0.90


def test_confidence_rounded_to_2dp() -> None:
    """Raw confidence 0.8567 → output confidence 0.86."""
    from ml.vision.inference import run_inference

    model = FakeModel([(0, 0.8567)])
    result = run_inference(_jpeg_bytes(), model=model)
    assert len(result.items) == 1
    assert result.items[0]["confidence"] == 0.86


def test_all_above_threshold_returned() -> None:
    """Five detections all above 0.50 → five items."""
    from ml.vision.inference import run_inference

    dets = [(0, 0.90), (1, 0.80), (2, 0.70), (3, 0.65), (4, 0.55)]
    model = FakeModel(dets)
    result = run_inference(_jpeg_bytes(), model=model)
    assert len(result.items) == 5


def test_high_threshold_boundary() -> None:
    """0.80 is HIGH, 0.799 is MEDIUM (just below boundary rounds to 0.80 after rounding)."""
    # Test at the schema level, not inference level
    assert get_confidence_level(0.80) == ConfidenceLevel.HIGH
    assert get_confidence_level(0.799) == ConfidenceLevel.MEDIUM


def test_medium_threshold_boundary() -> None:
    """0.50 is MEDIUM, 0.499 raises ValueError (DISCARD)."""
    assert get_confidence_level(0.50) == ConfidenceLevel.MEDIUM
    with pytest.raises(ValueError):
        get_confidence_level(0.499)
