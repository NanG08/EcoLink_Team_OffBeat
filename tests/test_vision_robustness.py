"""
Robustness, label-mapping and URL-security edge cases for ml.vision.inference / safety.
No weights, GPU or network required.
"""
import io
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from PIL import Image

from ml.vision.inference import (
    InferenceError,
    _label_for_class,
    _model_names,
    preprocess_image_bytes,
    run_inference,
)
from ml.vision.safety import is_allowed_host, validate_image_url
from ml.vision.schemas import load_classes_config


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────

class FakeBoxes:
    def __init__(self, detections):
        import torch

        self._dets = detections
        self.cls = torch.tensor([float(d[0]) for d in detections])
        self.conf = torch.tensor([float(d[1]) for d in detections])
        self.xyxy = torch.tensor([[10.0, 10.0, 100.0, 100.0] for _ in detections])

    def __len__(self):
        return len(self._dets)


class FakeResult:
    def __init__(self, detections, boxes_none=False):
        self.boxes = None if boxes_none else FakeBoxes(detections)


class FakeModel:
    """Fake YOLO model; *names* mimics ultralytics' ``model.names`` when given."""

    def __init__(self, detections, names=None, results=None):
        self._dets = detections
        self._results = results
        if names is not None:
            self.names = names
        self.calls = []

    def __call__(self, image, **kwargs):
        self.calls.append((image, kwargs))
        return self._results if self._results is not None else [FakeResult(self._dets)]


_ECOLINK_NAMES = {i: n for i, n in enumerate(["paper", "cardboard", "plastic", "metal", "glass", "ewaste", "battery"])}
# First entries of the COCO label table used by the pretrained yolov8n.pt fallback
_COCO_NAMES = {0: "person", 1: "bicycle", 2: "car", 5: "bus", 6: "train", 39: "bottle",
               41: "cup", 63: "laptop", 67: "cell phone", 73: "book", 65: "remote"}


def _encode(img: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _jpeg(w=64, h=64) -> bytes:
    return _encode(Image.new("RGB", (w, h), (120, 80, 40)), "JPEG")


# ─────────────────────────────────────────────────────────────────────────────
# Preprocessing robustness
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,fmt", [("RGBA", "PNG"), ("L", "PNG"), ("P", "PNG"), ("RGB", "WEBP"), ("L", "JPEG")])
def test_colour_modes_are_converted_to_rgb(mode, fmt) -> None:
    img = preprocess_image_bytes(_encode(Image.new(mode, (32, 24)), fmt))
    assert img.mode == "RGB"
    assert img.size == (32, 24)


@pytest.mark.parametrize("payload", [
    b"\xff\xd8\xff" + b"garbage" * 20,          # JPEG magic, not a JPEG
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,        # PNG magic, no chunks
    b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 30, # WebP magic, no payload
])
def test_corrupt_images_raise_inference_error_400(payload) -> None:
    with pytest.raises(InferenceError) as exc:
        preprocess_image_bytes(payload)
    assert exc.value.status_code == 400


def test_truncated_jpeg_raises_inference_error_400() -> None:
    data = _encode(Image.effect_noise((256, 256), 64).convert("RGB"), "JPEG")
    with pytest.raises(InferenceError) as exc:
        preprocess_image_bytes(data[: len(data) // 3])
    assert exc.value.status_code == 400


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_disallowed_formats_rejected(fmt) -> None:
    with pytest.raises(InferenceError, match="Unsupported"):
        preprocess_image_bytes(_encode(Image.new("RGB", (8, 8)), fmt))


def test_oversized_bytes_input_rejected() -> None:
    with pytest.raises(InferenceError, match="5 MB"):
        preprocess_image_bytes(b"\xff\xd8\xff" + b"\x00" * (5 * 1024 * 1024))


def test_empty_bytes_rejected() -> None:
    with pytest.raises(InferenceError):
        preprocess_image_bytes(b"")


# ─────────────────────────────────────────────────────────────────────────────
# run_inference edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_model_receives_configured_thresholds_and_pil_image() -> None:
    from ml.vision.inference import _INF_CFG

    model = FakeModel([])
    run_inference(_jpeg(), model=model)
    image, kwargs = model.calls[0]
    assert isinstance(image, Image.Image) and image.mode == "RGB"
    assert kwargs["conf"] == _INF_CFG["confidence"]["detection_threshold"] == 0.50
    assert kwargs["iou"] == _INF_CFG["nms"]["iou_threshold"]
    assert kwargs["max_det"] == _INF_CFG["nms"]["max_detections"]
    assert kwargs["imgsz"] == _INF_CFG["model"]["image_size"]
    assert kwargs["verbose"] is False


def test_no_detections_returns_empty_items() -> None:
    assert run_inference(_jpeg(), model=FakeModel([])).to_api_dict() == {"items": []}


def test_boxes_none_is_handled() -> None:
    model = FakeModel([], results=[FakeResult([], boxes_none=True)])
    assert run_inference(_jpeg(), model=model).items == []


def test_multiple_result_objects_are_merged_and_deduplicated() -> None:
    model = FakeModel([], results=[FakeResult([(2, 0.60), (6, 0.90)]), FakeResult([(2, 0.85)])])
    items = run_inference(_jpeg(), model=model).items
    assert [(i["label"], i["confidence"]) for i in items] == [("battery", 0.9), ("plastic", 0.85)]


def test_out_of_range_class_id_is_skipped() -> None:
    items = run_inference(_jpeg(), model=FakeModel([(42, 0.95), (3, 0.70)])).items
    assert [i["label"] for i in items] == ["metal"]


def test_boundary_confidence_050_is_kept() -> None:
    items = run_inference(_jpeg(), model=FakeModel([(4, 0.50)])).items
    assert items == [{"label": "glass", "confidence": 0.5, "hazardous": False}]


def test_every_output_label_is_canonical_and_hazard_flag_is_deterministic() -> None:
    cfg = load_classes_config()
    dets = [(i, 0.55 + 0.05 * i) for i in range(7)]
    items = run_inference(_jpeg(), model=FakeModel(dets)).items
    assert {i["label"] for i in items} == set(cfg.class_names)
    for item in items:
        assert item["hazardous"] is cfg.is_hazardous(item["label"])
        assert 0.5 <= item["confidence"] <= 1.0


def test_ewaste_and_battery_together_both_hazardous() -> None:
    items = run_inference(_jpeg(), model=FakeModel([(5, 0.88), (6, 0.77)])).items
    assert all(i["hazardous"] for i in items) and len(items) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Class-name based label mapping (guards against mismatched weights)
# ─────────────────────────────────────────────────────────────────────────────

def test_model_names_parsing() -> None:
    assert _model_names(FakeModel([], names={0: "a", 1: "b"})) == {0: "a", 1: "b"}
    assert _model_names(FakeModel([], names=["a", "b"])) == {0: "a", 1: "b"}
    assert _model_names(FakeModel([])) is None


def test_fine_tuned_names_map_one_to_one() -> None:
    cfg = load_classes_config()
    for cid, name in _ECOLINK_NAMES.items():
        assert _label_for_class(cid, _ECOLINK_NAMES, cfg) == name


def test_coco_pretrained_model_is_never_mislabelled_by_position() -> None:
    """Pretrained COCO yolov8n: 'bus' (id 5) must NOT become hazardous 'ewaste',
    'person' (id 0) must NOT become 'paper'."""
    model = FakeModel([(0, 0.9), (5, 0.9), (6, 0.8), (2, 0.8)], names=_COCO_NAMES)
    assert run_inference(_jpeg(), model=model).items == []


def test_coco_names_are_translated_through_dataset_mapping() -> None:
    model = FakeModel([(67, 0.9), (39, 0.8), (73, 0.7), (65, 0.95)], names=_COCO_NAMES)
    items = run_inference(_jpeg(), model=model).items
    assert [(i["label"], i["hazardous"]) for i in items] == [
        ("ewaste", True),     # cell phone
        ("plastic", False),   # bottle (dataset_mapping default)
        ("paper", False),     # book
    ]                         # 'remote' has no mapping -> dropped


def test_explicitly_discarded_source_class_is_dropped() -> None:
    cfg = load_classes_config()
    assert _label_for_class(0, {0: "trash"}, cfg) is None      # mapped to null
    assert _label_for_class(0, {0: "food"}, cfg) is None       # in discard list
    assert _label_for_class(0, {0: "Plastic Bottle"}, cfg) == "plastic"  # case/space normalised


# ─────────────────────────────────────────────────────────────────────────────
# URL / SSRF edge cases
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://bucket.s3.amazonaws.com@evil.com/x.jpg",          # userinfo trick
    "https://bucket.s3.amazonaws.com.evil.com/x.jpg",          # suffix trick
    "https://evilamazonaws.com/x.jpg",                         # missing dot
    "https://[::1]/x.jpg",                                     # IPv6 loopback
    "https://127.0.0.1/x.jpg",
    "https://0.0.0.0/x.jpg",
    "ftp://bucket.s3.amazonaws.com/x.jpg",
    "file:///etc/passwd",
    "https:///x.jpg",
    "",
])
def test_hostile_urls_rejected(url) -> None:
    with pytest.raises(ValueError):
        validate_image_url(url)


def test_hostname_check_is_case_insensitive() -> None:
    assert is_allowed_host("Bucket.S3.AMAZONAWS.com")
    validate_image_url("https://Bucket.S3.AMAZONAWS.COM/x.jpg")


def test_presigned_query_string_and_port_are_allowed() -> None:
    validate_image_url("https://bucket.s3.ap-south-1.amazonaws.com:443/a/b.jpg?X-Amz-Signature=abc&X-Amz-Expires=300")


@pytest.mark.xfail(strict=True, reason=(
    "Known gap: fetch_image_bytes uses urllib's default opener, which follows 3xx "
    "redirects without re-validating the target, so an allowed *.amazonaws.com host "
    "can redirect the fetch to 169.254.169.254 (SSRF). Fix: open via a "
    "HTTPRedirectHandler subclass whose redirect_request() calls validate_image_url()."))
def test_redirect_to_metadata_endpoint_is_not_followed() -> None:
    import urllib.request
    from email.message import Message

    from ml.vision.safety import fetch_image_bytes

    requested = []

    def https_open(self, req):
        requested.append(req.full_url)
        headers = Message()
        headers["Location"] = "http://169.254.169.254/latest/meta-data/"
        resp = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
        resp.msg = "Found"
        return resp

    def http_open(self, req):
        requested.append(req.full_url)
        return urllib.response.addinfourl(io.BytesIO(b"secret"), Message(), req.full_url, 200)

    import urllib.response
    with mock.patch.object(urllib.request.HTTPSHandler, "https_open", https_open), \
         mock.patch.object(urllib.request.HTTPHandler, "http_open", http_open), \
         mock.patch.object(urllib.request, "_opener", None):
        try:
            fetch_image_bytes("https://bucket.s3.amazonaws.com/redirect.jpg")
        except ValueError:
            pass
    assert not any("169.254.169.254" in u for u in requested), requested
