"""
Unit tests for ml.vision.inference — uses mocks, no model weights required.
"""
import io
import os
import sys
import unittest.mock as mock

# Ensure repo root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from PIL import Image

from ml.vision.schemas import ClassifyResponse, ClassesConfig, load_classes_config
from ml.vision.inference import (
    InferenceError,
    _sniff_format,
    preprocess_image_from_url,
    run_inference,
    classify_image_url,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fake YOLO model helpers
# ─────────────────────────────────────────────────────────────────────────────

class FakeBoxes:
    """Mimics ultralytics result.boxes with .cls, .conf, .xyxy tensors."""

    def __init__(self, detections: list[tuple[int, float]]):
        """
        detections: list of (class_id, confidence)
        """
        import torch

        self._dets = detections
        self.cls = torch.tensor([float(d[0]) for d in detections])
        self.conf = torch.tensor([float(d[1]) for d in detections])
        # Fake xyxy: simple non-zero boxes
        self.xyxy = torch.tensor([[10.0, 10.0, 100.0, 100.0] for _ in detections])

    def __len__(self) -> int:
        return len(self._dets)


class FakeResult:
    def __init__(self, detections: list[tuple[int, float]]):
        self.boxes = FakeBoxes(detections)


class FakeModel:
    """Fake YOLO model whose __call__ returns a single FakeResult."""

    def __init__(self, detections: list[tuple[int, float]]):
        self._dets = detections

    def __call__(self, image, **kwargs):
        return [FakeResult(self._dets)]


# ─────────────────────────────────────────────────────────────────────────────
# _sniff_format tests
# ─────────────────────────────────────────────────────────────────────────────

def test_sniff_jpeg() -> None:
    data = b"\xff\xd8\xff" + b"\x00" * 10
    assert _sniff_format(data) == "jpeg"


def test_sniff_png() -> None:
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    assert _sniff_format(data) == "png"


def test_sniff_webp() -> None:
    data = b"RIFF" + b"\x00" * 4 + b"WEBP"
    assert _sniff_format(data) == "webp"


def test_sniff_unknown() -> None:
    assert _sniff_format(b"\x00\x01\x02\x03") == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# run_inference tests
# ─────────────────────────────────────────────────────────────────────────────

def _small_jpeg_bytes() -> bytes:
    """Return minimal valid JPEG bytes."""
    buf = io.BytesIO()
    img = Image.new("RGB", (64, 64), color=(128, 64, 32))
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_run_inference_returns_classify_response() -> None:
    """run_inference with a fake model returns a ClassifyResponse."""
    model = FakeModel([(2, 0.88)])   # plastic, high confidence
    result = run_inference(_small_jpeg_bytes(), model=model)
    assert isinstance(result, ClassifyResponse)
    assert len(result.items) == 1
    item = result.items[0]
    assert "label" in item
    assert "confidence" in item
    assert "hazardous" in item


def test_hazardous_tagged_correctly() -> None:
    """battery (class_id=6) with high confidence → hazardous=True."""
    model = FakeModel([(6, 0.90)])
    result = run_inference(_small_jpeg_bytes(), model=model)
    assert len(result.items) == 1
    assert result.items[0]["hazardous"] is True
    assert result.items[0]["label"] == "battery"


def test_non_hazardous_tagged_correctly() -> None:
    """paper (class_id=0) with high confidence → hazardous=False."""
    model = FakeModel([(0, 0.85)])
    result = run_inference(_small_jpeg_bytes(), model=model)
    assert len(result.items) == 1
    assert result.items[0]["hazardous"] is False
    assert result.items[0]["label"] == "paper"


def test_confidence_below_threshold_discarded() -> None:
    """Detection at conf=0.30 (< 0.50 threshold) must be dropped."""
    model = FakeModel([(2, 0.30)])
    result = run_inference(_small_jpeg_bytes(), model=model)
    assert result.items == []


def test_duplicate_label_keeps_highest_confidence() -> None:
    """Two plastic detections → only the higher-confidence one survives."""
    model = FakeModel([(2, 0.70), (2, 0.85)])
    result = run_inference(_small_jpeg_bytes(), model=model)
    assert len(result.items) == 1
    assert result.items[0]["confidence"] == 0.85


def test_sorted_descending_by_confidence() -> None:
    """Multiple detections → sorted by confidence descending."""
    model = FakeModel([(0, 0.75), (1, 0.92), (2, 0.60)])
    result = run_inference(_small_jpeg_bytes(), model=model)
    confs = [item["confidence"] for item in result.items]
    assert confs == sorted(confs, reverse=True)


def test_api_dict_shape() -> None:
    """to_api_dict() returns only the 'items' key at top level."""
    model = FakeModel([(0, 0.80)])
    result = run_inference(_small_jpeg_bytes(), model=model)
    api = result.to_api_dict()
    assert set(api.keys()) == {"items"}


def test_multiple_items_all_returned() -> None:
    """5 different classes all above threshold → 5 items."""
    model = FakeModel([(0, 0.90), (1, 0.80), (2, 0.70), (3, 0.60), (4, 0.55)])
    result = run_inference(_small_jpeg_bytes(), model=model)
    assert len(result.items) == 5


# ─────────────────────────────────────────────────────────────────────────────
# preprocess_image_from_url tests
# ─────────────────────────────────────────────────────────────────────────────

def test_inference_error_for_non_https_url() -> None:
    with pytest.raises(InferenceError, match="HTTPS"):
        preprocess_image_from_url("http://bucket.s3.amazonaws.com/img.jpg")


def test_inference_error_for_non_amazonaws_host() -> None:
    with pytest.raises(InferenceError):
        preprocess_image_from_url("https://evil.example.com/img.jpg")


def _make_mock_urlopen_response(data: bytes):
    resp = mock.MagicMock()
    resp.read.return_value = data
    resp.headers = {"Content-Type": "image/jpeg"}
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    return resp


@mock.patch("ml.vision.safety.urllib.request.urlopen")
def test_oversized_image_raises(mock_urlopen: mock.MagicMock) -> None:
    oversized = b"\xff\xd8\xff" + b"X" * (5 * 1024 * 1024 + 1)
    mock_urlopen.return_value = _make_mock_urlopen_response(oversized)
    with pytest.raises(InferenceError):
        preprocess_image_from_url("https://bucket.s3.amazonaws.com/big.jpg")


@mock.patch("ml.vision.safety.urllib.request.urlopen")
def test_unknown_format_raises(mock_urlopen: mock.MagicMock) -> None:
    bad_data = b"\x00\x01\x02\x03" + b"X" * 50
    mock_urlopen.return_value = _make_mock_urlopen_response(bad_data)
    with pytest.raises(InferenceError, match="format|Unsupported"):
        preprocess_image_from_url("https://bucket.s3.amazonaws.com/img.bmp")


@mock.patch("ml.vision.safety.urllib.request.urlopen")
def test_valid_url_returns_pil_image(mock_urlopen: mock.MagicMock) -> None:
    jpeg_data = _small_jpeg_bytes()
    mock_urlopen.return_value = _make_mock_urlopen_response(jpeg_data)
    img = preprocess_image_from_url("https://bucket.s3.amazonaws.com/img.jpg")
    assert isinstance(img, Image.Image)
    assert img.mode == "RGB"


# ─────────────────────────────────────────────────────────────────────────────
# classify_image_url tests (full pipeline mock)
# ─────────────────────────────────────────────────────────────────────────────

@mock.patch("ml.vision.inference.get_model")
@mock.patch("ml.vision.inference.preprocess_image_from_url")
def test_classify_image_url_returns_dict(
    mock_preprocess: mock.MagicMock,
    mock_get_model: mock.MagicMock,
) -> None:
    """classify_image_url chains preprocess + inference and returns a dict."""
    mock_preprocess.return_value = Image.new("RGB", (64, 64))
    mock_get_model.return_value = FakeModel([(0, 0.85)])

    result = classify_image_url("https://bucket.s3.amazonaws.com/img.jpg")
    assert isinstance(result, dict)
    assert "items" in result


@mock.patch("ml.vision.inference.get_model")
@mock.patch("ml.vision.inference.preprocess_image_from_url")
def test_classify_image_url_calls_preprocess(
    mock_preprocess: mock.MagicMock,
    mock_get_model: mock.MagicMock,
) -> None:
    mock_preprocess.return_value = Image.new("RGB", (64, 64))
    mock_get_model.return_value = FakeModel([(1, 0.90)])

    classify_image_url("https://bucket.s3.amazonaws.com/img.jpg")
    mock_preprocess.assert_called_once_with("https://bucket.s3.amazonaws.com/img.jpg")
