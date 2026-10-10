"""
Integration tests with the real fine-tuned YOLOv8n weights and the prepared test split.

Skipped automatically when ultralytics, the weights or the dataset are missing.
Run only these with:  pytest -m integration
"""
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import yaml

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parent.parent
_VISION = _REPO / "ml" / "vision"
_WEIGHTS = _REPO / yaml.safe_load((_VISION / "config" / "inference.yaml").read_text())["model"]["weights_path"]
_TEST_DIR = _VISION / "data" / "test"
_COCO_WEIGHTS = _VISION / "artifacts" / "weights" / "yolov8n.pt"

# Minimum quality gates on the held-out test split (image-level, pipeline output).
# Deliberately loose: they catch a broken model or label mapping, not small regressions.
_MIN_MACRO_RECALL = 0.50
_MIN_HAZARD_RECALL = 0.70
_MAX_WARM_LATENCY_MS = 2000


def _test_images(limit=None):
    imgs = sorted(p for p in (_TEST_DIR / "images").glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    return imgs[:limit] if limit else imgs


def _gt_labels(img: Path, names):
    lines = (_TEST_DIR / "labels" / f"{img.stem}.txt").read_text().splitlines()
    return {names[int(line.split()[0])] for line in lines if line.strip()}


@pytest.fixture(scope="module")
def model():
    pytest.importorskip("ultralytics")
    if not _WEIGHTS.exists():
        pytest.skip(f"fine-tuned weights not found at {_WEIGHTS}")
    from ultralytics import YOLO

    return YOLO(str(_WEIGHTS))


@pytest.fixture(scope="module")
def cfg():
    from ml.vision.schemas import load_classes_config

    return load_classes_config()


@pytest.fixture(scope="module")
def predictions(model, cfg):
    """Pipeline output for every test image: list of (gt_labels, api_items)."""
    from ml.vision.inference import run_inference

    images = _test_images()
    if not images:
        pytest.skip("prepared test split is empty")
    return [(_gt_labels(p, cfg.class_names), run_inference(p.read_bytes(), model=model).items) for p in images]


def test_weights_class_table_matches_classes_yaml(model, cfg) -> None:
    """IDs baked into the weights must equal classes.yaml order, or labels shift silently."""
    assert [model.names[i] for i in sorted(model.names)] == cfg.class_names


def test_inference_image_size_matches_training(model) -> None:
    """Inferring at a different size than training sharply cuts recall (416→640 halved it)."""
    trained = (getattr(model, "ckpt", None) or {}).get("train_args", {}).get("imgsz")
    if trained is None:
        pytest.skip("checkpoint does not record its training image size")
    configured = yaml.safe_load((_VISION / "config" / "inference.yaml").read_text())["model"]["image_size"]
    assert configured == trained, f"inference.yaml image_size={configured}, weights trained at {trained}"


def test_api_contract_on_real_images(predictions, cfg) -> None:
    for _, items in predictions:
        confs = [i["confidence"] for i in items]
        assert confs == sorted(confs, reverse=True)
        assert len({i["label"] for i in items}) == len(items)
        for item in items:
            assert set(item) == {"label", "confidence", "hazardous"}
            assert item["label"] in cfg.class_names
            assert 0.5 <= item["confidence"] <= 1.0 and round(item["confidence"], 2) == item["confidence"]
            assert item["hazardous"] is cfg.is_hazardous(item["label"])


def test_every_class_is_detected_somewhere(predictions, cfg) -> None:
    predicted = {i["label"] for _, items in predictions for i in items}
    assert predicted == set(cfg.class_names), f"never predicted: {set(cfg.class_names) - predicted}"


def test_image_level_macro_recall_floor(predictions, cfg) -> None:
    recalls = []
    for cls in cfg.class_names:
        support = [(gt, items) for gt, items in predictions if cls in gt]
        hits = sum(cls in {i["label"] for i in items} for _, items in support)
        recalls.append(hits / len(support))
    assert statistics.mean(recalls) >= _MIN_MACRO_RECALL, dict(zip(cfg.class_names, recalls))


def test_hazardous_items_are_flagged(predictions, cfg) -> None:
    """Images containing e-waste or batteries must yield at least one hazardous item."""
    hazardous = [items for gt, items in predictions if gt & cfg.hazardous_labels]
    flagged = sum(any(i["hazardous"] for i in items) for items in hazardous)
    assert flagged / len(hazardous) >= _MIN_HAZARD_RECALL, f"{flagged}/{len(hazardous)} flagged"


def test_warm_cpu_latency_budget(model) -> None:
    from ml.vision.inference import run_inference

    data = [p.read_bytes() for p in _test_images(limit=10)]
    run_inference(data[0], model=model)  # warm-up
    times = []
    for d in data:
        t = time.perf_counter()
        run_inference(d, model=model)
        times.append((time.perf_counter() - t) * 1000)
    assert statistics.median(times) < _MAX_WARM_LATENCY_MS, times


def test_coco_pretrained_fallback_never_mislabels() -> None:
    """The COCO yolov8n fallback must not report a bus as e-waste or a person as paper."""
    pytest.importorskip("ultralytics")
    if not _COCO_WEIGHTS.exists():
        pytest.skip("COCO yolov8n.pt not cached locally")
    from ultralytics import YOLO
    from ultralytics.utils import ASSETS

    from ml.vision.inference import run_inference

    labels = [i["label"] for i in run_inference((ASSETS / "bus.jpg").read_bytes(), model=YOLO(str(_COCO_WEIGHTS))).items]
    assert "ewaste" not in labels and "paper" not in labels, labels
