"""Local waste detector: the YOLOv8n ONNX export from ml/vision, run with ONNX Runtime.

Used by core/classifier.py when the Bedrock classifier is unavailable (or when
CLASSIFY_PRIMARY=onnx). It follows the same rules as ml/vision (config/inference.yaml,
safety.py): input 416x416, detection threshold 0.50, class-aware NMS at IoU 0.45, at most
50 detections, one entry per label (the most confident), hazardous labels tagged, items
sorted by confidence, confidence rounded to 2 decimals.

Pre/post-processing is plain numpy + Pillow, so the Lambda layer needs only
onnxruntime, numpy and Pillow (no torch / ultralytics).
"""

from __future__ import annotations

import ast
import io
import threading
from pathlib import Path
from typing import Any

from ecolink.core.constants import HAZARDOUS_LABELS, LABELS
from ecolink.core.http import bad_request
from ecolink.core.model_store import VISION_MODEL_KEY, ModelStore, ModelUnavailable

DETECTION_THRESHOLD = 0.50  # ml/vision/config/inference.yaml: confidence.detection_threshold
IOU_THRESHOLD = 0.45  # nms.iou_threshold
MAX_DETECTIONS = 50  # nms.max_detections
LETTERBOX_PAD_VALUE = 114  # what ultralytics pads with
SOURCE = "onnx-yolov8n"


# ── pure numpy helpers (unit-tested without a model) ───────────────────────────

def resize_bilinear(arr: Any, new_w: int, new_h: int) -> Any:
    """Bilinear resize that matches OpenCV's cv2.resize(..., INTER_LINEAR) (no anti-aliasing).

    The YOLO weights were trained on images resized with OpenCV and ultralytics preprocesses with it.
    Pillow's BILINEAR low-pass filters when shrinking, which feeds the model different pixels and shifts
    its confidences, so the resize is done here with the same half-pixel sampling as OpenCV.
    """
    import numpy as np

    h, w = arr.shape[:2]
    xs = (np.arange(new_w) + 0.5) * (w / new_w) - 0.5
    ys = (np.arange(new_h) + 0.5) * (h / new_h) - 0.5
    x0, y0 = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
    wx = (xs - x0).astype(np.float32)[None, :, None]
    wy = (ys - y0).astype(np.float32)[:, None, None]
    x0c, x1c = np.clip(x0, 0, w - 1), np.clip(x0 + 1, 0, w - 1)
    y0c, y1c = np.clip(y0, 0, h - 1), np.clip(y0 + 1, 0, h - 1)
    src = arr.astype(np.float32)
    rows0, rows1 = src[y0c], src[y1c]
    top = rows0[:, x0c] * (1 - wx) + rows0[:, x1c] * wx
    bottom = rows1[:, x0c] * (1 - wx) + rows1[:, x1c] * wx
    return np.clip(np.rint(top * (1 - wy) + bottom * wy), 0, 255).astype(np.uint8)


def letterbox(img: Any, size: int) -> tuple[Any, float, tuple[int, int]]:
    """Resize keeping aspect ratio, pad to size x size. Returns (HWC uint8, ratio, (left, top))."""
    import numpy as np

    w, h = img.size
    ratio = min(size / h, size / w)
    new_w, new_h = int(round(w * ratio)), int(round(h * ratio))
    pixels = np.asarray(img)
    if (new_w, new_h) != (w, h):
        pixels = resize_bilinear(pixels, new_w, new_h)
    pad_w, pad_h = (size - new_w) / 2, (size - new_h) / 2
    left, top = int(round(pad_w - 0.1)), int(round(pad_h - 0.1))
    canvas = np.full((size, size, 3), LETTERBOX_PAD_VALUE, dtype=np.uint8)
    canvas[top:top + new_h, left:left + new_w] = pixels
    return canvas, ratio, (left, top)


def to_input_tensor(canvas: Any) -> Any:
    import numpy as np

    return (canvas.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]


def iou_one_to_many(box: Any, others: Any) -> Any:
    import numpy as np

    x1 = np.maximum(box[0], others[:, 0])
    y1 = np.maximum(box[1], others[:, 1])
    x2 = np.minimum(box[2], others[:, 2])
    y2 = np.minimum(box[3], others[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area = (box[2] - box[0]) * (box[3] - box[1])
    areas = (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1])
    return inter / np.maximum(area + areas - inter, 1e-9)


def nms(boxes: Any, scores: Any, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression; returns kept indices, best first."""
    import numpy as np

    order = np.argsort(-scores)
    keep: list[int] = []
    while order.size:
        best = order[0]
        keep.append(int(best))
        if order.size == 1:
            break
        rest = order[1:]
        order = rest[iou_one_to_many(boxes[best], boxes[rest]) <= iou_threshold]
    return keep


def decode(
    output: Any,
    names: dict[int, str],
    *,
    conf_threshold: float = DETECTION_THRESHOLD,
    iou_threshold: float = IOU_THRESHOLD,
    max_det: int = MAX_DETECTIONS,
) -> list[dict[str, Any]]:
    """Turn a raw YOLOv8 output (1, 4+nc, N) into [{label, confidence, hazardous}].

    Rows are (cx, cy, w, h, class scores...). Class-aware NMS, then one entry per label.
    """
    import numpy as np

    preds = np.asarray(output)[0].T  # (N, 4 + nc)
    scores_all = preds[:, 4:]
    class_ids = scores_all.argmax(axis=1)
    confidences = scores_all.max(axis=1)
    mask = confidences >= conf_threshold
    if not mask.any():
        return []
    preds, class_ids, confidences = preds[mask], class_ids[mask], confidences[mask]
    cx, cy, w, h = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)

    kept: list[int] = []
    for class_id in np.unique(class_ids):
        idx = np.where(class_ids == class_id)[0]
        kept.extend(int(idx[i]) for i in nms(boxes[idx], confidences[idx], iou_threshold))
    kept.sort(key=lambda i: -confidences[i])
    kept = kept[:max_det]

    best: dict[str, float] = {}
    for i in kept:
        label = names.get(int(class_ids[i]), "").strip().lower().replace(" ", "_").replace("-", "_")
        if label not in LABELS:
            continue  # a class with no EcoLink equivalent
        best[label] = max(best.get(label, 0.0), float(confidences[i]))
    return [
        {"label": label, "confidence": round(conf, 2), "hazardous": label in HAZARDOUS_LABELS}
        for label, conf in sorted(best.items(), key=lambda kv: -kv[1])
        if conf >= conf_threshold
    ]


def parse_names(raw: str | None) -> dict[int, str]:
    """The ONNX file carries its class table as the string "{0: 'paper', 1: 'cardboard', ...}"."""
    if raw:
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, dict):
                return {int(k): str(v) for k, v in parsed.items()}
            if isinstance(parsed, (list, tuple)):
                return {i: str(v) for i, v in enumerate(parsed)}
        except (ValueError, SyntaxError):
            pass
    return {i: name for i, name in enumerate(LABELS)}  # EcoLink's frozen class order


# ── the detector ───────────────────────────────────────────────────────────────

class OnnxDetector:
    def __init__(self, model_path: str | Path) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ModelUnavailable("onnxruntime is not installed in this runtime") from exc
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self._session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        spec = self._session.get_inputs()[0]
        self._input_name = spec.name
        self.size = int(spec.shape[2]) if isinstance(spec.shape[2], int) else 416
        self.names = parse_names(self._session.get_modelmeta().custom_metadata_map.get("names"))

    def detect(self, image_bytes: bytes) -> list[dict[str, Any]]:
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.load()
            img = img.convert("RGB")
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            raise bad_request(f"Corrupt or unreadable image: {exc}") from exc
        canvas, _ratio, _pad = letterbox(img, self.size)
        output = self._session.run(None, {self._input_name: to_input_tensor(canvas)})[0]
        return decode(output, self.names)


_detector: OnnxDetector | None = None
_lock = threading.Lock()


def get_detector(store: ModelStore | None = None) -> OnnxDetector:
    """Singleton per container: downloads the weights from S3 on first use."""
    global _detector
    with _lock:
        if _detector is None:
            store = store or ModelStore()
            import os

            _detector = OnnxDetector(store.local_path(os.environ.get("VISION_MODEL_KEY", VISION_MODEL_KEY)))
        return _detector


def reset() -> None:
    global _detector
    _detector = None


def classify_bytes(image_bytes: bytes, detector: OnnxDetector | None = None) -> list[dict[str, Any]]:
    """Public entry used by core/classifier.py. Raises ModelUnavailable if the model cannot run."""
    return (detector or get_detector()).detect(image_bytes)
