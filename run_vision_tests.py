"""
Runner for EcoLink vision pipeline tests.
Executes tests directly without pytest to bypass broken pytest plugins.
"""
import sys
import os
import io
import traceback
import unittest.mock as mock

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

_passed = 0
_failed = 0


def ok(name):
    global _passed
    _passed += 1
    print(f"  PASS: {name}")


def fail(name, err):
    global _failed
    _failed += 1
    print(f"  FAIL: {name}")
    print(f"        {err}")


def run(name, fn):
    try:
        fn()
        ok(name)
    except Exception as e:
        fail(name, traceback.format_exc().strip().splitlines()[-1])


# ─────────────────────────────────────────────────────────────────────────────
# schemas tests
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== test_vision_schema ===")

from ml.vision.schemas import (
    BoundingBox, ClassesConfig, ClassifyRequest, ClassifyResponse,
    ConfidenceLevel, Detection, confidence_tier,
    detection_to_public, detections_to_response, load_classes_config,
)

cfg = load_classes_config()

def t(): expected = ["paper","cardboard","plastic","metal","glass","ewaste","battery"]; assert cfg.class_names == expected
run("class_names_order", t)

def t(): assert cfg.id_for("ewaste") == 5; assert cfg.id_for("battery") == 6
run("id_for_ewaste_battery", t)

def t(): assert cfg.label_for(0) == "paper"; assert cfg.label_for(6) == "battery"
run("label_for_id", t)

def t():
    try: cfg.id_for("gold"); assert False
    except KeyError: pass
run("unknown_label_raises_KeyError", t)

def t():
    assert cfg.is_hazardous("ewaste") and cfg.is_hazardous("battery")
    assert not cfg.is_hazardous("paper") and not cfg.is_hazardous("plastic")
run("hazardous_set", t)

def t(): assert confidence_tier(0.90, cfg) == "HIGH"
run("confidence_tier_high", t)

def t(): assert confidence_tier(0.65, cfg) == "MEDIUM"
run("confidence_tier_medium", t)

def t(): assert confidence_tier(0.40, cfg) == "DISCARD"
run("confidence_tier_discard", t)

def t(): assert confidence_tier(0.80, cfg) == "HIGH"; assert confidence_tier(0.50, cfg) == "MEDIUM"
run("confidence_tier_boundaries", t)

def t():
    bbox = BoundingBox(1.0, 2.0, 3.0, 4.0)
    assert bbox.x1 == 1.0 and bbox.y1 == 2.0 and bbox.x2 == 3.0 and bbox.y2 == 4.0
run("bounding_box_fields", t)

def t():
    assert ConfidenceLevel.HIGH == "high"; assert ConfidenceLevel.MEDIUM == "medium"
run("confidence_level_enum_values", t)

def t():
    det = Detection("plastic",2,0.8567,BoundingBox(0,0,1,1),ConfidenceLevel.HIGH,False,False)
    pub = detection_to_public(det)
    assert pub == {"label":"plastic","confidence":0.86,"hazardous":False}
run("detection_to_public_rounding", t)

def t():
    det = Detection("battery",6,0.91,BoundingBox(0,0,1,1),ConfidenceLevel.HIGH,True,False)
    pub = detection_to_public(det)
    assert pub["hazardous"] is True and pub["label"] == "battery"
run("detection_to_public_hazardous", t)

def t():
    det = Detection("plastic",2,0.88,BoundingBox(0,0,1,1),ConfidenceLevel.HIGH,False,False)
    result = detections_to_response([det])
    assert "items" in result and len(result["items"]) == 1
run("detections_to_response_shape", t)

def t():
    resp = ClassifyResponse(items=[{"label":"plastic","confidence":0.88,"hazardous":False}])
    api = resp.to_api_dict()
    assert set(api.keys()) == {"items"}
run("classify_response_to_api_dict", t)

def t():
    req = ClassifyRequest(image_url="https://bucket.s3.amazonaws.com/img.jpg")
    assert req.image_url
run("classify_request_has_image_url", t)


# ─────────────────────────────────────────────────────────────────────────────
# safety tests
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== test_vision_safety ===")

from ml.vision.safety import (
    CLASS_NAMES, HAZARDOUS_CLASSES, DETECTION_THRESHOLD,
    HIGH_CONFIDENCE_THRESHOLD, HAZARDOUS_CONFIRMATION_THRESHOLD,
    apply_safety_rules, build_detection, fetch_image_bytes,
    get_confidence_level, is_allowed_host, is_hazardous,
    requires_confirmation, validate_image_url,
)

def t(): assert is_allowed_host("bucket.s3.amazonaws.com")
run("allowed_amazonaws_subdomain", t)

def t(): assert is_allowed_host("s3.amazonaws.com")
run("allowed_amazonaws_root", t)

def t(): assert not is_allowed_host("evil.example.com")
run("blocked_non_aws", t)

def t(): validate_image_url("https://mybucket.s3.amazonaws.com/images/foo.jpg")
run("valid_presigned_url_passes", t)

def t():
    try: validate_image_url("http://bucket.s3.amazonaws.com/x.jpg"); assert False
    except ValueError: pass
run("blocked_http_scheme", t)

def t():
    try: validate_image_url("https://localhost/x.jpg"); assert False
    except ValueError: pass
run("blocked_localhost", t)

def t():
    try: validate_image_url("https://169.254.169.254/latest/meta-data"); assert False
    except ValueError: pass
run("blocked_link_local", t)

def t():
    try: validate_image_url("https://10.0.0.1/x.jpg"); assert False
    except ValueError: pass
run("blocked_private_10", t)

def t():
    try: validate_image_url("https://192.168.1.1/x.jpg"); assert False
    except ValueError: pass
run("blocked_private_192_168", t)

def t():
    assert is_allowed_host("cdn.myapp.com", extra_hosts=frozenset({"cdn.myapp.com"}))
    assert not is_allowed_host("other.com", extra_hosts=frozenset({"cdn.myapp.com"}))
run("extra_hosts_allow_list", t)

def t(): assert is_hazardous("battery") and is_hazardous("ewaste") and not is_hazardous("paper")
run("hazardous_detection", t)

def t(): assert requires_confirmation("battery", 0.60) is True
run("battery_low_conf_requires_confirmation", t)

def t(): assert requires_confirmation("battery", 0.97) is False
run("battery_high_conf_no_confirmation", t)

def t(): assert requires_confirmation("plastic", 0.75) is True
run("plastic_medium_conf_requires_confirmation", t)

def t(): assert requires_confirmation("plastic", 0.90) is False
run("plastic_high_conf_no_confirmation", t)

def t(): assert get_confidence_level(0.90) == ConfidenceLevel.HIGH
run("get_confidence_level_high", t)

def t(): assert get_confidence_level(0.60) == ConfidenceLevel.MEDIUM
run("get_confidence_level_medium", t)

def t():
    try: get_confidence_level(0.30); assert False
    except ValueError: pass
run("get_confidence_level_below_raises", t)

def t():
    det = Detection("plastic",2,0.30,BoundingBox(0,0,100,100),ConfidenceLevel.MEDIUM,False,False)
    det2 = Detection("paper",0,0.70,BoundingBox(0,0,100,100),ConfidenceLevel.MEDIUM,False,False)
    result = apply_safety_rules([det, det2])
    assert len(result) == 1 and result[0].label == "paper"
run("apply_safety_rules_filters_below_threshold", t)

def t():
    d1 = Detection("plastic",2,0.50,BoundingBox(0,0,100,100),ConfidenceLevel.MEDIUM,False,False)
    d2 = Detection("paper",0,0.85,BoundingBox(0,0,100,100),ConfidenceLevel.HIGH,False,False)
    d3 = Detection("battery",6,0.92,BoundingBox(0,0,100,100),ConfidenceLevel.HIGH,True,False)
    result = apply_safety_rules([d1, d2, d3])
    assert len(result) == 3
run("apply_safety_rules_preserves_above_threshold", t)

def t(): assert CLASS_NAMES == ["paper","cardboard","plastic","metal","glass","ewaste","battery"]
run("class_names_constant", t)

def t(): assert "ewaste" in HAZARDOUS_CLASSES and "battery" in HAZARDOUS_CLASSES
run("hazardous_classes_set", t)


# ─────────────────────────────────────────────────────────────────────────────
# confidence tests
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== test_confidence ===")

from ml.vision.safety import build_detection

def t(): assert get_confidence_level(0.90) == ConfidenceLevel.HIGH
run("confidence_090_is_high", t)

def t(): assert get_confidence_level(0.80) == ConfidenceLevel.HIGH
run("confidence_080_is_high_boundary", t)

def t(): assert get_confidence_level(0.79) == ConfidenceLevel.MEDIUM
run("confidence_079_is_medium", t)

def t(): assert get_confidence_level(0.50) == ConfidenceLevel.MEDIUM
run("confidence_050_is_medium_boundary", t)

def t():
    try: get_confidence_level(0.49); assert False
    except ValueError: pass
run("confidence_049_raises_ValueError", t)

def t():
    det = build_detection("battery", 6, 0.62, (0,0,100,100))
    assert det.hazardous is True and det.requires_confirmation is True
run("battery_062_hazardous_requires_confirmation", t)

def t():
    det = build_detection("battery", 6, 0.97, (0,0,100,100))
    assert det.hazardous is True and det.requires_confirmation is False
run("battery_097_hazardous_no_confirmation", t)

def t():
    det = build_detection("plastic", 2, 0.75, (0,0,100,100))
    assert det.hazardous is False and det.requires_confirmation is True
run("plastic_075_not_hazardous_requires_confirmation", t)

def t():
    det = build_detection("plastic", 2, 0.95, (0,0,100,100))
    assert det.hazardous is False and det.requires_confirmation is False
run("plastic_095_not_hazardous_no_confirmation", t)

def t():
    det = build_detection("glass", 4, 0.88, (5.0,10.0,200.0,300.0))
    assert det.label == "glass" and det.class_id == 4
    assert det.bbox.x1 == 5.0 and det.bbox.y2 == 300.0
    assert det.confidence_level == ConfidenceLevel.HIGH
run("build_detection_all_fields", t)


# ─────────────────────────────────────────────────────────────────────────────
# class mapping tests
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== test_class_mapping ===")
import yaml
from pathlib import Path
_CONFIG_DIR = Path(REPO_ROOT) / "ml" / "vision" / "config"

with open(_CONFIG_DIR / "dataset_mapping.yaml") as fh:
    _map_data = yaml.safe_load(fh)
mapping = _map_data.get("mappings", {})
discard_list = _map_data.get("discard", [])

with open(_CONFIG_DIR / "classes.yaml") as fh:
    classes_data = yaml.safe_load(fh)

def t(): assert isinstance(mapping, dict) and len(mapping) > 0
run("mapping_file_loads", t)

def t(): assert mapping.get("plastic_bottle") == "plastic"
run("plastic_bottle_maps_to_plastic", t)

def t(): assert mapping.get("battery") == "battery"
run("battery_maps_to_battery", t)

def t(): assert mapping.get("batteries") == "battery"
run("batteries_maps_to_battery", t)

def t(): assert mapping.get("smartphone") == "ewaste"
run("smartphone_maps_to_ewaste", t)

def t(): assert mapping.get("glass_bottle") == "glass"
run("glass_bottle_maps_to_glass", t)

def t(): assert mapping.get("drink_can") == "metal"
run("drink_can_maps_to_metal", t)

def t(): assert "unknown_class" not in mapping
run("unknown_class_not_in_mappings", t)

def t():
    canonical = set(cfg.class_names)
    for k, v in mapping.items():
        if v is not None:
            assert v in canonical, f"'{k}'->'{v}' not canonical"
run("all_non_null_values_are_canonical", t)

def t():
    mapped_targets = {v for v in mapping.values() if v is not None}
    for cls in cfg.class_names:
        assert cls in mapped_targets, f"No mapping for '{cls}'"
run("all_7_canonical_classes_in_values", t)

def t(): assert len(classes_data["class_names"]) == 7
run("classes_yaml_has_7_names", t)

def t(): assert classes_data["class_ids"]["paper"] == 0
run("class_ids_paper_is_0", t)

def t(): assert classes_data["class_ids"]["battery"] == 6
run("class_ids_battery_is_6", t)

def t(): assert classes_data["class_ids"]["ewaste"] == 5
run("class_ids_ewaste_is_5", t)

def t():
    key = "hazardous_classes" if "hazardous_classes" in classes_data else "hazardous_labels"
    assert "ewaste" in classes_data[key] and "battery" in classes_data[key]
run("hazardous_classes_contains_ewaste_battery", t)


# ─────────────────────────────────────────────────────────────────────────────
# inference tests (with mocked model)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== test_vision_inference ===")

from ml.vision.inference import InferenceError, _sniff_format, run_inference, classify_image_url, preprocess_image_from_url
from ml.vision.schemas import ClassifyResponse
import torch
from PIL import Image

class FakeBoxes:
    def __init__(self, dets):
        self.cls = torch.tensor([float(d[0]) for d in dets])
        self.conf = torch.tensor([float(d[1]) for d in dets])
        self.xyxy = torch.tensor([[10.,10.,100.,100.] for _ in dets])
    def __len__(self): return len(self.cls)

class FakeResult:
    def __init__(self, dets): self.boxes = FakeBoxes(dets)

class FakeModel:
    def __init__(self, dets): self._dets = dets
    def __call__(self, img, **kw): return [FakeResult(self._dets)]

def jpeg_bytes(w=64, h=64):
    buf = io.BytesIO()
    Image.new("RGB",(w,h)).save(buf,format="JPEG")
    return buf.getvalue()

def t(): assert _sniff_format(b"\xff\xd8\xff\x00") == "jpeg"
run("sniff_jpeg", t)

def t(): assert _sniff_format(b"\x89PNG\r\n\x1a\n") == "png"
run("sniff_png", t)

def t(): assert _sniff_format(b"RIFF\x00\x00\x00\x00WEBP") == "webp"
run("sniff_webp", t)

def t(): assert _sniff_format(b"\x00\x01\x02\x03") == "unknown"
run("sniff_unknown", t)

def t():
    result = run_inference(jpeg_bytes(), model=FakeModel([(2,0.88)]))
    assert isinstance(result, ClassifyResponse)
    item = result.items[0]
    assert set(item.keys()) == {"label","confidence","hazardous"}
run("run_inference_returns_classify_response", t)

def t():
    result = run_inference(jpeg_bytes(), model=FakeModel([(6,0.90)]))
    assert result.items[0]["hazardous"] is True and result.items[0]["label"] == "battery"
run("hazardous_tagged_correctly_battery", t)

def t():
    result = run_inference(jpeg_bytes(), model=FakeModel([(0,0.85)]))
    assert result.items[0]["hazardous"] is False and result.items[0]["label"] == "paper"
run("non_hazardous_tagged_correctly", t)

def t():
    result = run_inference(jpeg_bytes(), model=FakeModel([(2,0.30)]))
    assert result.items == []
run("confidence_below_threshold_discarded", t)

def t():
    result = run_inference(jpeg_bytes(), model=FakeModel([(2,0.70),(2,0.85)]))
    assert len(result.items) == 1 and result.items[0]["confidence"] == 0.85
run("duplicate_label_keeps_highest_confidence", t)

def t():
    result = run_inference(jpeg_bytes(), model=FakeModel([(0,0.75),(1,0.92),(2,0.60)]))
    confs = [i["confidence"] for i in result.items]
    assert confs == sorted(confs, reverse=True)
run("sorted_descending_by_confidence", t)

def t():
    result = run_inference(jpeg_bytes(), model=FakeModel([(0,0.80)]))
    assert set(result.to_api_dict().keys()) == {"items"}
run("api_dict_shape", t)

def t():
    try: preprocess_image_from_url("http://bucket.s3.amazonaws.com/img.jpg"); assert False
    except InferenceError: pass
run("inference_error_for_non_https_url", t)

def t():
    try: preprocess_image_from_url("https://evil.example.com/img.jpg"); assert False
    except InferenceError: pass
run("inference_error_for_non_amazonaws_host", t)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"Results: {_passed} passed, {_failed} failed")
print(f"{'='*50}")
sys.exit(0 if _failed == 0 else 1)
