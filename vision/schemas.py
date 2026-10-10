"""
Shared dataclass schemas and config loader for the EcoLink vision pipeline.

Zero heavy dependencies: stdlib + PyYAML only.
Safe to import in test environments without GPU or model weights.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).parent / "config"

# Module-level cache: resolved path → ClassesConfig instance
_cache: dict[Path, "ClassesConfig"] = {}


@dataclass
class ClassesConfig:
    """Loaded from ml/vision/config/classes.yaml."""

    class_names: list[str]
    hazardous_labels: frozenset[str]
    confidence_thresholds: dict[str, float]

    # ── derived helpers ──────────────────────────────────────────────────────

    def id_for(self, label: str) -> int:
        """Return the stable numeric class ID for *label*. Raises KeyError if unknown."""
        try:
            return self.class_names.index(label)
        except ValueError:
            raise KeyError(label) from None

    def label_for(self, id: int) -> str:
        """Return the label for numeric class ID. Raises IndexError if out of range."""
        return self.class_names[id]  # raises IndexError naturally

    def is_hazardous(self, label: str) -> bool:
        """Return True if *label* is in the hazardous set."""
        return label in self.hazardous_labels


def load_classes_config(path: Path | None = None) -> ClassesConfig:
    """
    Load and cache the classes configuration from *path* (default: config/classes.yaml).

    Subsequent calls with the same resolved path return the cached instance.
    """
    resolved = (path or _CONFIG_DIR / "classes.yaml").resolve()
    if resolved in _cache:
        return _cache[resolved]

    with open(resolved, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    cfg = ClassesConfig(
        class_names=data["class_names"],
        hazardous_labels=frozenset(data.get("hazardous_classes", data.get("hazardous_labels", []))),
        confidence_thresholds=data.get("confidence_thresholds", {"high": 0.80, "medium": 0.50, "discard": 0.0}),
    )
    _cache[resolved] = cfg
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Confidence tier helper
# ─────────────────────────────────────────────────────────────────────────────

def confidence_tier(score: float, cfg: ClassesConfig | None = None) -> str:
    """
    Return ``"HIGH"``, ``"MEDIUM"``, or ``"DISCARD"`` based on *score*.

    Uses thresholds from *cfg* (loads default config if None).
    """
    if cfg is None:
        cfg = load_classes_config()
    high = cfg.confidence_thresholds.get("high", 0.80)
    medium = cfg.confidence_thresholds.get("medium", 0.50)
    if score >= high:
        return "HIGH"
    if score >= medium:
        return "MEDIUM"
    return "DISCARD"


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"


@dataclass
class BoundingBox:
    """Bounding box in pixel coordinates (xyxy format)."""

    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class Detection:
    """Single object detection result from the YOLO pipeline."""

    label: str
    class_id: int
    confidence: float               # raw float (rounded when serialised)
    bbox: BoundingBox
    confidence_level: ConfidenceLevel
    hazardous: bool
    requires_confirmation: bool


@dataclass
class ClassifyRequest:
    """Incoming request payload for POST /classify."""

    image_url: str


@dataclass
class ClassifyResponse:
    """Public API response for POST /classify."""

    items: list[dict]               # list of detection_to_public() dicts

    def to_api_dict(self) -> dict:
        """Serialise to the public API contract: ``{"items": [...]}``. """
        return {"items": self.items}


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def detection_to_public(det: Detection) -> dict:
    """
    Convert a Detection to the public API dict format.

    Returns: ``{"label": str, "confidence": float, "hazardous": bool}``
    """
    return {
        "label": det.label,
        "confidence": round(det.confidence, 2),
        "hazardous": det.hazardous,
    }


def detections_to_response(dets: list[Detection]) -> dict:
    """
    Convert a list of Detections to the public API response dict.

    Returns: ``{"items": [...]}``
    """
    return {"items": [detection_to_public(d) for d in dets]}
