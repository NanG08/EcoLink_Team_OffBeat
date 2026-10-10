"""
Singleton model loader for the EcoLink vision pipeline.

Two backends are supported:
  • *.pt  → ultralytics.YOLO (local dev / training)
  • *.onnx → OnnxModel wrapper around onnxruntime (Lambda / production)

The backend is chosen automatically from the file extension.
Both expose the same callable interface so inference.py needs no changes:

    results = model(pil_image, conf=0.5, iou=0.45, max_det=50, imgsz=416, verbose=False)
    for result in results:
        boxes = result.boxes
        for i in range(len(boxes)):
            cls   = int(boxes.cls[i].item())
            conf  = float(boxes.conf[i].item())
            xyxy  = boxes.xyxy[i].tolist()
    model.names  →  {0: "paper", 1: "cardboard", ...}

Thread-safe via threading.Lock.  Import is safe with no model weights on disk —
FileNotFoundError is raised only when get_model() / load_model() is actually called.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from ml.vision.paths import repo_path

_CONFIG_DIR = Path(__file__).parent / "config"

_lock: threading.Lock = threading.Lock()
_model: Any = None  # OnnxModel | ultralytics.YOLO | None

# ─────────────────────────────────────────────────────────────────────────────
# ONNX Runtime backend
# ─────────────────────────────────────────────────────────────────────────────

# Canonical class names in training order — baked into best.onnx weights.
# Must match classes.yaml (which cannot be imported here to keep this module
# free of heavy dependencies at import time).
_CANONICAL_NAMES: dict[int, str] = {
    0: "paper",
    1: "cardboard",
    2: "plastic",
    3: "metal",
    4: "glass",
    5: "ewaste",
    6: "battery",
}


class _Boxes:
    """
    Minimal drop-in replacement for ultralytics ``Boxes`` used by inference.py.

    Wraps three plain numpy arrays and exposes the .item() / .tolist()
    interface that inference.py calls on each detection.
    """

    def __init__(self, cls_arr, conf_arr, xyxy_arr) -> None:
        # Each is a 1-D numpy array of length N (number of detections).
        self._cls = cls_arr
        self._conf = conf_arr
        self._xyxy = xyxy_arr  # shape (N, 4)

    def __len__(self) -> int:
        return len(self._cls)

    # --- per-detection accessors (inference.py uses boxes.cls[i].item() etc.) ---

    class _ScalarProxy:
        """Wraps a Python scalar to provide .item() without numpy."""
        def __init__(self, v):
            self._v = v

        def item(self):
            return self._v

        def tolist(self):
            return self._v

    class _RowProxy:
        """Wraps a list row to provide .tolist()."""
        def __init__(self, row):
            self._row = row

        def tolist(self):
            return list(self._row)

    @property
    def cls(self):
        return [self._ScalarProxy(int(v)) for v in self._cls]

    @property
    def conf(self):
        return [self._ScalarProxy(float(v)) for v in self._conf]

    @property
    def xyxy(self):
        return [self._RowProxy(row) for row in self._xyxy]


class _Result:
    """Single-image result container matching the ultralytics Result interface."""

    def __init__(self, boxes: _Boxes) -> None:
        self.boxes = boxes


def _nms_numpy(boxes_xyxy, scores, iou_threshold: float) -> list[int]:
    """
    Pure-numpy Non-Maximum Suppression.

    Args:
        boxes_xyxy: numpy array shape (N, 4) in [x1, y1, x2, y2] format.
        scores:     numpy array shape (N,) with confidence scores.
        iou_threshold: IoU threshold above which the lower-scoring box is suppressed.

    Returns:
        List of kept indices (sorted by score descending).
    """
    import numpy as np

    if len(scores) == 0:
        return []

    order = scores.argsort()[::-1]
    kept: list[int] = []

    x1, y1, x2, y2 = (boxes_xyxy[:, i] for i in range(4))
    areas = (x2 - x1) * (y2 - y1)

    while len(order) > 0:
        i = order[0]
        kept.append(int(i))
        if len(order) == 1:
            break

        rest = order[1:]
        ix1 = np.maximum(x1[i], x1[rest])
        iy1 = np.maximum(y1[i], y1[rest])
        ix2 = np.minimum(x2[i], x2[rest])
        iy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, ix2 - ix1) * np.maximum(0.0, iy2 - iy1)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]

    return kept


class OnnxModel:
    """
    ONNX Runtime inference wrapper with the same callable interface as
    ultralytics.YOLO, so inference.py works without modification.

    Handles the standard YOLOv8 ONNX output layout:
        output shape [1, num_classes + 4, num_anchors]
        first 4 rows = cx, cy, w, h (centre format)
        rows 4..end  = per-class logits / scores (already sigmoid-normalised
                       in YOLOv8 exports)
    """

    def __init__(self, weights_path: str) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.log_severity_level = 3  # suppress verbose ONNX Runtime logs in CloudWatch
        self._session = ort.InferenceSession(
            weights_path,
            sess_options=so,
            providers=["CPUExecutionProvider"],
        )
        self._input_name: str = self._session.get_inputs()[0].name
        # shape is e.g. [1, 3, 416, 416]
        self._input_shape: list[int] = self._session.get_inputs()[0].shape
        self.names: dict[int, str] = dict(_CANONICAL_NAMES)

    def __call__(
        self,
        image,  # PIL.Image.Image
        *,
        conf: float = 0.5,
        iou: float = 0.45,
        max_det: int = 50,
        imgsz: int = 416,
        verbose: bool = False,  # noqa: ARG002
    ) -> list[_Result]:
        import numpy as np
        from PIL import Image

        # ── Preprocess ────────────────────────────────────────────────────────
        if not isinstance(image, Image.Image):
            raise TypeError(f"Expected PIL.Image, got {type(image)}")

        img = image.resize((imgsz, imgsz), Image.BILINEAR)
        arr = np.array(img, dtype=np.float32) / 255.0          # [H, W, 3]
        arr = arr.transpose(2, 0, 1)[np.newaxis, ...]           # [1, 3, H, W]

        # ── Run ONNX inference ────────────────────────────────────────────────
        raw = self._session.run(None, {self._input_name: arr})[0]  # [1, 11, 8400]
        raw = raw[0]  # [11, 8400]  (drop batch dim)

        num_classes = len(self.names)
        cx, cy, bw, bh = raw[0], raw[1], raw[2], raw[3]
        class_scores = raw[4:4 + num_classes]  # [num_classes, 8400]

        # Best class per anchor
        class_ids = class_scores.argmax(axis=0)       # [8400]
        max_scores = class_scores.max(axis=0)         # [8400]

        # ── Confidence filter ─────────────────────────────────────────────────
        mask = max_scores >= conf
        if not mask.any():
            return [_Result(_Boxes(
                cls_arr=np.empty(0, dtype=np.int32),
                conf_arr=np.empty(0, dtype=np.float32),
                xyxy_arr=np.empty((0, 4), dtype=np.float32),
            ))]

        cx_f = cx[mask]; cy_f = cy[mask]
        bw_f = bw[mask]; bh_f = bh[mask]
        cls_f = class_ids[mask].astype(np.int32)
        scr_f = max_scores[mask]

        # Centre → corner format, scaled to image pixels
        x1 = (cx_f - bw_f / 2) * imgsz
        y1 = (cy_f - bh_f / 2) * imgsz
        x2 = (cx_f + bw_f / 2) * imgsz
        y2 = (cy_f + bh_f / 2) * imgsz
        xyxy = np.stack([x1, y1, x2, y2], axis=1)  # [N, 4]

        # ── NMS ───────────────────────────────────────────────────────────────
        kept = _nms_numpy(xyxy, scr_f, iou)[:max_det]

        boxes = _Boxes(
            cls_arr=cls_f[kept],
            conf_arr=scr_f[kept],
            xyxy_arr=xyxy[kept],
        )
        return [_Result(boxes)]


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_device(device: str) -> str:
    """Resolve ``'auto'`` to ``'cuda'`` or ``'cpu'`` at runtime."""
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _load_inference_config() -> dict:
    path = _CONFIG_DIR / "inference.yaml"
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _existing(path: str | None) -> str | None:
    """Return *path* if it exists as given or relative to the repo root, else None."""
    if not path:
        return None
    if Path(path).exists():
        return str(path)
    candidate = repo_path(path)
    return str(candidate) if candidate.exists() else None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def load_model(weights_path: str | None = None, device: str = "auto") -> Any:
    """
    Load and return the singleton model (thread-safe).

    Backend selection (by file extension):
      .onnx → OnnxModel (onnxruntime, no torch required)
      .pt   → ultralytics.YOLO (torch required)

    Resolution order for weights:
    1. *weights_path* argument (if provided and exists on disk)
    2. ``model.weights_path`` from ``inference.yaml`` (if file exists on disk)
    3. Download pretrained ``yolov8n.pt`` via ultralytics (requires internet).

    Args:
        weights_path: Optional path to ``.pt`` or ``.onnx`` weights file.
        device: ``"auto"`` (default), ``"cpu"``, or ``"cuda"``.
                Ignored for ONNX backend (always CPU on Lambda).

    Returns:
        OnnxModel or ultralytics.YOLO instance.
    """
    global _model  # noqa: PLW0603

    with _lock:
        if _model is not None:
            return _model

        # Determine which weights file to use
        weights: str
        explicit = _existing(weights_path)
        if explicit:
            weights = explicit
        else:
            try:
                cfg = _load_inference_config()
                weights = _existing(cfg["model"]["weights_path"]) or "yolov8n.pt"
            except Exception:
                weights = "yolov8n.pt"

        # Choose backend by extension
        if weights.endswith(".onnx"):
            _model = OnnxModel(weights)
        else:
            from ultralytics import YOLO  # lazy import — keeps module import cheap
            resolved_device = _resolve_device(device)
            m = YOLO(weights)
            m.to(resolved_device)
            _model = m

        return _model


def get_model() -> Any:
    """
    Return the cached model, loading it on first call using inference.yaml settings.
    """
    if _model is None:
        return load_model()
    return _model


def reset_model() -> None:
    """
    Clear the module-level model singleton (used in tests to ensure isolation).
    """
    global _model  # noqa: PLW0603
    with _lock:
        _model = None
