"""
Singleton YOLOv8n model loader for the EcoLink vision pipeline.

Loads once per process (warm Lambda container reuse).
Thread-safe via threading.Lock.

Import is safe with no model weights on disk.
Raises FileNotFoundError only when get_model() is actually called
and neither custom weights nor the default yolov8n.pt download succeeds.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from ml.vision.paths import repo_path

_CONFIG_DIR = Path(__file__).parent / "config"

_lock: threading.Lock = threading.Lock()
_model: Any = None   # ultralytics.YOLO or None


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


def load_model(weights_path: str | None = None, device: str = "auto") -> Any:
    """
    Load and return the singleton YOLO model (thread-safe).

    Resolution order for weights (relative paths are tried as given, then
    against the repo root, so loading does not depend on the CWD):
    1. *weights_path* argument (if provided and exists on disk)
    2. ``model.weights_path`` from ``inference.yaml`` (if file exists on disk)
    3. Download pretrained ``yolov8n.pt`` via ultralytics (requires internet on first use).
       This is a COCO model: inference maps its class names through
       ``dataset_mapping.yaml`` and drops classes with no EcoLink equivalent.

    Args:
        weights_path: Optional path to fine-tuned ``.pt`` weights file.
        device: ``"auto"`` (default), ``"cpu"``, or ``"cuda"``.

    Returns:
        Loaded ``ultralytics.YOLO`` instance.
    """
    global _model  # noqa: PLW0603

    resolved_device = _resolve_device(device)

    with _lock:
        if _model is not None:
            return _model

        from ultralytics import YOLO  # lazy import — keeps module import cheap

        # Determine which weights to load
        weights: str
        explicit = _existing(weights_path)
        if explicit:
            weights = explicit
        else:
            # Fall back to inference.yaml default
            try:
                cfg = _load_inference_config()
                weights = _existing(cfg["model"]["weights_path"]) or "yolov8n.pt"
            except Exception:
                weights = "yolov8n.pt"  # ultralytics will download pretrained nano

        _model = YOLO(weights)
        _model.to(resolved_device)
        return _model


def get_model() -> Any:
    """
    Return the cached YOLO model, loading it on first call.

    Uses settings from ``ml/vision/config/inference.yaml``.
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
