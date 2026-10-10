"""
Core inference logic for the EcoLink waste detection pipeline.

Converts a raw image (bytes or HTTPS URL) into a ClassifyResponse,
applying confidence filtering, label mapping, hazardous tagging, and
deduplication.

Does NOT import from ecolink.* — fully standalone.
"""
from __future__ import annotations

import io
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from PIL import Image

from ml.vision.schemas import (
    ClassifyResponse,
    Detection,
    detections_to_response,
    load_classes_config,
)
from ml.vision.safety import (
    CLASS_NAMES,
    DETECTION_THRESHOLD,
    apply_safety_rules,
    build_detection,
    fetch_image_bytes,
    validate_image_url,
)
from ml.vision.model_loader import get_model

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).parent / "config"

with open(_CONFIG_DIR / "inference.yaml", encoding="utf-8") as _f:
    _INF_CFG = yaml.safe_load(_f)

_MAX_BYTES: int = _INF_CFG["preprocessing"]["max_image_bytes"]
_ALLOWED_FORMATS: set[str] = set(_INF_CFG["preprocessing"]["allowed_formats"])
_IOU_THRESHOLD: float = _INF_CFG["nms"]["iou_threshold"]
_MAX_DETECTIONS: int = _INF_CFG["nms"]["max_detections"]
_IMAGE_SIZE: int = _INF_CFG["model"]["image_size"]

with open(_CONFIG_DIR / "dataset_mapping.yaml", encoding="utf-8") as _f:
    _SOURCE_MAPPINGS: dict[str, str | None] = yaml.safe_load(_f).get("mappings", {})

# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────


class InferenceError(Exception):
    """Raised when the inference pipeline cannot process a request."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ─────────────────────────────────────────────────────────────────────────────
# Image format detection
# ─────────────────────────────────────────────────────────────────────────────


def _sniff_format(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Preprocessing
# ─────────────────────────────────────────────────────────────────────────────


def preprocess_image_from_url(image_url: str) -> Image.Image:
    """
    Download an image from *image_url* (HTTPS + *.amazonaws.com only).

    Validates content-type, size, and format. Returns an RGB PIL Image.

    Raises:
        InferenceError: URL validation, size, or format violation.
    """
    # URL security check (raises ValueError on failure)
    try:
        validate_image_url(image_url)
    except ValueError as exc:
        raise InferenceError(str(exc), status_code=400) from exc

    try:
        data = fetch_image_bytes(image_url, max_bytes=_MAX_BYTES)
    except ValueError as exc:
        raise InferenceError(str(exc), status_code=400) from exc
    except urllib.error.HTTPError as exc:
        raise InferenceError(f"HTTP {exc.code} fetching image", status_code=502) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InferenceError(f"Could not fetch image: {exc}", status_code=502) from exc

    return preprocess_image_bytes(data)


def preprocess_image_bytes(data: bytes) -> Image.Image:
    """
    Convert raw image *data* (bytes) into an RGB PIL Image.

    Validates size, format and decodability, and converts colour mode.

    Raises:
        InferenceError: (400) oversized, unsupported, corrupt or truncated image.
    """
    if len(data) > _MAX_BYTES:
        raise InferenceError("Image exceeds 5 MB limit", status_code=400)

    img_format = _sniff_format(data)
    if img_format not in _ALLOWED_FORMATS:
        raise InferenceError(
            f"Unsupported image format: {img_format}; use JPEG, PNG, or WebP",
            status_code=400,
        )
    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # decode now so truncation surfaces here, not inside the model
        if img.mode != "RGB":
            img = img.convert("RGB")
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        # UnidentifiedImageError is an OSError subclass
        raise InferenceError(f"Corrupt or unreadable image: {exc}", status_code=400) from exc
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Inference
# ─────────────────────────────────────────────────────────────────────────────


def _model_names(model: Any) -> dict[int, str] | None:
    """Return the model's ``{class_id: name}`` table, or None if it has none."""
    names = getattr(model, "names", None)
    if isinstance(names, dict):
        return {int(k): str(v) for k, v in names.items()}
    if isinstance(names, (list, tuple)):
        return {i: str(v) for i, v in enumerate(names)}
    return None


def _label_for_class(class_id: int, model_names: dict[int, str] | None, cfg) -> str | None:
    """
    Map a model class ID to an EcoLink canonical label, or None to drop it.

    The model's own class name is looked up (canonical names pass through,
    other names go through ``dataset_mapping.yaml``), so weights trained on a
    different label set — e.g. the COCO ``yolov8n.pt`` fallback, whose ID 5 is
    "bus" — can never be mislabelled by position.  Models that expose no
    names are assumed to use EcoLink's canonical IDs.
    """
    if model_names is None:
        try:
            return cfg.label_for(class_id)
        except IndexError:
            return None
    raw = model_names.get(class_id)
    if raw is None:
        return None
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if key in cfg.class_names:
        return key
    mapped = _SOURCE_MAPPINGS.get(key)
    return mapped if mapped in cfg.class_names else None


def run_inference(
    image_input: bytes | str,
    cfg=None,
    model: Any = None,
    infer_cfg: dict | None = None,
) -> ClassifyResponse:
    """
    Run YOLOv8n inference on *image_input* and return a :class:`ClassifyResponse`.

    Args:
        image_input: Raw image bytes or an HTTPS URL string.
        cfg: :class:`ClassesConfig` (loads default if None).
        model: YOLO model instance (uses singleton if None).
        infer_cfg: Inference config dict (loads inference.yaml if None).

    Returns:
        :class:`ClassifyResponse` with items sorted by confidence descending.

    Class-ID → label mapping goes through the model's own class names and
    ``dataset_mapping.yaml`` (see :func:`_label_for_class`) — never via
    hard-coded strings or bare positional IDs.
    """
    if cfg is None:
        cfg = load_classes_config()
    if infer_cfg is None:
        infer_cfg = _INF_CFG

    conf_threshold = infer_cfg["confidence"]["detection_threshold"]
    iou_threshold = infer_cfg["nms"]["iou_threshold"]
    max_det = infer_cfg["nms"]["max_detections"]
    imgsz = infer_cfg["model"]["image_size"]

    # Obtain PIL image (validate input before paying for a model load)
    if isinstance(image_input, str):
        image = preprocess_image_from_url(image_input)
    else:
        image = preprocess_image_bytes(image_input)

    if model is None:
        model = get_model()

    # Run YOLO prediction
    results = model(
        image,
        conf=conf_threshold,
        iou=iou_threshold,
        max_det=max_det,
        imgsz=imgsz,
        verbose=False,
    )

    # Parse raw YOLO output — keep highest-confidence detection per label
    best_per_label: dict[str, Detection] = {}
    model_names = _model_names(model)

    for result in results:
        if result.boxes is None or len(result.boxes) == 0:
            continue
        boxes = result.boxes
        for i in range(len(boxes)):
            class_id = int(boxes.cls[i].item())
            confidence = float(boxes.conf[i].item())

            label = _label_for_class(class_id, model_names, cfg)
            if label is None:
                continue  # class with no EcoLink equivalent — skip

            xyxy = boxes.xyxy[i].tolist()
            det = build_detection(label, class_id, confidence, tuple(xyxy))

            # Deduplication: keep highest-confidence detection per label
            existing = best_per_label.get(label)
            if existing is None or det.confidence > existing.confidence:
                best_per_label[label] = det

    detections = list(best_per_label.values())

    # Apply safety rules (threshold filter + derived-field recompute)
    detections = apply_safety_rules(detections)

    # Sort by confidence descending
    detections.sort(key=lambda d: -d.confidence)

    # Build public API response
    items = [
        {
            "label": d.label,
            "confidence": round(d.confidence, 2),
            "hazardous": d.hazardous,
        }
        for d in detections
    ]
    return ClassifyResponse(items=items)


# ─────────────────────────────────────────────────────────────────────────────
# High-level pipeline entry point
# ─────────────────────────────────────────────────────────────────────────────


def classify_image_url(image_url: str) -> dict:
    """
    Full pipeline: URL → preprocess → inference → public response dict.

    Returns: ``{"items": [{"label": ..., "confidence": ..., "hazardous": ...}]}``
    """
    response = run_inference(image_url)
    return response.to_api_dict()
