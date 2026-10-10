"""
Tests for ml/vision/scripts/classification_report.py (per-class accuracy table)
and ml/vision/export.py (ONNX export, integration).
"""
import importlib.util
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from PIL import Image

from ml.vision.scripts import classification_report as cr

NAMES = ["paper", "cardboard", "plastic", "metal", "glass", "ewaste", "battery"]
HAZ = {"ewaste", "battery"}
_REPO = Path(__file__).resolve().parent.parent


def items(*labels):
    return [{"label": l, "confidence": 0.9, "hazardous": l in HAZ} for l in labels]


RECORDS = [
    ({"paper"}, items("paper")),                 # correct
    ({"paper"}, items()),                        # missed
    ({"plastic"}, items("glass")),               # confused
    ({"battery"}, items("battery")),             # hazard hit
    ({"ewaste", "metal"}, items("metal")),       # hazard missed
    ({"glass"}, items("glass", "plastic")),      # extra false positive
]


def test_per_class_counts_and_metrics() -> None:
    r = cr.score(RECORDS, NAMES, HAZ)
    p = r["per_class"]["paper"]
    assert (p["tp"], p["fp"], p["fn"], p["tn"]) == (1, 0, 1, 4)
    assert (p["precision"], p["recall"], p["f1"], p["accuracy"], p["support"]) == (1.0, 0.5, 0.6667, 0.8333, 2)
    pl = r["per_class"]["plastic"]
    assert (pl["tp"], pl["fp"], pl["fn"]) == (0, 1, 1) and pl["f1"] == 0.0 and pl["weak"] is True
    assert r["per_class"]["battery"]["f1"] == 1.0 and r["per_class"]["battery"]["weak"] is False
    card = r["per_class"]["cardboard"]
    assert (card["support"], card["tp"], card["tn"]) == (0, 0, 6)


def test_overall_hazard_and_confusion() -> None:
    r = cr.score(RECORDS, NAMES, HAZ)
    assert r["images"] == 6
    assert r["exact_match_accuracy"] == round(2 / 6, 4)
    assert r["images_with_no_prediction"] == 1
    assert r["hazard_detection"] == {"precision": 1.0, "recall": 0.5, "missed_images": 1, "hazardous_images": 2}
    conf = r["top1_confusion_single_class"]
    assert conf["paper"] == {"paper": 1, "none": 1}
    assert conf["plastic"] == {"glass": 1}
    assert conf["ewaste"] == {} and conf["metal"] == {}   # the multi-label image is excluded
    assert "plastic" in r["weak_classes"] and "battery" not in r["weak_classes"]


def test_markdown_table_and_png(tmp_path) -> None:
    r = cr.score(RECORDS, NAMES, HAZ)
    md = cr.to_markdown(r, "Demo")
    assert md.startswith("# Demo")
    assert "| paper | 0.833 | 1.000 | 0.500 | 0.667 | 2 |" in md
    assert "| plastic |" in md and "**weak**" in md
    png = tmp_path / "cm.png"
    cr.save_confusion_png(r, NAMES, png, "Demo")
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_load_split_reads_ground_truth(tmp_path) -> None:
    (tmp_path / "images").mkdir()
    (tmp_path / "labels").mkdir()
    Image.new("RGB", (8, 8)).save(tmp_path / "images" / "a.jpg")
    Image.new("RGB", (8, 8)).save(tmp_path / "images" / "b.png")
    (tmp_path / "labels" / "a.txt").write_text("2 0.5 0.5 0.1 0.1\n6 0.2 0.2 0.1 0.1\n")
    (tmp_path / "images" / "notes.txt").write_text("ignored")
    split = cr.load_split(tmp_path, NAMES)
    assert [(p.name, gt) for p, gt in split] == [("a.jpg", {"plastic", "battery"}), ("b.png", set())]


@pytest.mark.integration
def test_onnx_export_is_numerically_identical(tmp_path) -> None:
    for pkg in ("onnx", "onnxruntime", "ultralytics"):
        if importlib.util.find_spec(pkg) is None:
            pytest.skip(f"{pkg} not installed")
    weights = _REPO / "ml" / "vision" / "artifacts" / "weights" / "best.pt"
    images = sorted((_REPO / "ml" / "vision" / "data" / "test" / "images").glob("*.jpg"))[:6]
    if not weights.exists() or not images:
        pytest.skip("needs best.pt and the prepared test split")
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    from ml.vision import export

    local = tmp_path / "best.pt"
    shutil.copy2(weights, local)
    onnx_path = export.export(local, "onnx", export._INF_CFG["model"]["image_size"])
    assert onnx_path.exists() and onnx_path.suffix == ".onnx"
    parity = export.numerical_parity(local, onnx_path, images, export._INF_CFG["model"]["image_size"])
    assert parity["passed"], parity
