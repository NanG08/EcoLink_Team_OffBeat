"""
Tests for ml/vision/scripts/prepare_dataset.py and validate_dataset.py using
tiny synthetic datasets in tmp_path (YOLO, COCO and Pascal VOC inputs).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from PIL import Image

from ml.vision.scripts import prepare_dataset as prep
from ml.vision.scripts import validate_dataset as val

_CONFIG = Path(__file__).parent.parent / "ml" / "vision" / "config"


@pytest.fixture(scope="module")
def cfg():
    mappings, discard = prep.load_mapping(_CONFIG / "dataset_mapping.yaml")
    return mappings, discard, prep.load_classes(_CONFIG / "classes.yaml")


def _img(path: Path, colour=(10, 20, 30), size=(64, 48)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, "JPEG")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# label resolution
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source,expected", [
    ("plastic_bottle", "plastic"), ("Plastic_Bottle", "plastic"), ("  smartphone ", "ewaste"),
    ("batteries", "battery"), ("glass", "glass"), ("pizza_box", "cardboard"), ("tin_can", "metal"),
])
def test_resolve_label_maps_to_canonical(cfg, source, expected) -> None:
    mappings, discard, names = cfg
    assert prep.resolve_label(source, mappings, discard, names) == names.index(expected)


@pytest.mark.parametrize("source", ["food", "organic", "trash", "biodegradable", "unknown_widget", ""])
def test_resolve_label_discards(cfg, source) -> None:
    mappings, discard, names = cfg
    assert prep.resolve_label(source, mappings, discard, names) is None


def test_resolve_id_with_and_without_source_classes(cfg) -> None:
    mappings, discard, names = cfg
    assert prep.resolve_id(3, mappings, discard, names) == 3
    assert prep.resolve_id(7, mappings, discard, names) is None
    assert prep.resolve_id(-1, mappings, discard, names) is None
    src = ["food", "smartphone", "glass_jar"]
    assert prep.resolve_id(0, mappings, discard, names, src) is None
    assert prep.resolve_id(1, mappings, discard, names, src) == names.index("ewaste")
    assert prep.resolve_id(2, mappings, discard, names, src) == names.index("glass")
    assert prep.resolve_id(3, mappings, discard, names, src) is None


@pytest.mark.parametrize("bbox,ok", [
    ((0.5, 0.5, 0.2, 0.2), True), ((1.0, 1.0, 1.0, 1.0), True),
    ((0.0, 0.5, 0.2, 0.2), False), ((0.5, 0.5, 0.0, 0.2), False),
    ((1.2, 0.5, 0.2, 0.2), False), ((0.5, 0.5, 0.2, -0.1), False),
])
def test_validate_yolo_bbox(bbox, ok) -> None:
    assert prep.validate_yolo_bbox(*bbox) is ok


# ─────────────────────────────────────────────────────────────────────────────
# format parsers
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_yolo_numeric_and_named_classes(tmp_path, cfg) -> None:
    mappings, discard, names = cfg
    _img(tmp_path / "images" / "a.jpg")
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "a.txt").write_text(
        "2 0.5 0.5 0.2 0.2\n"           # numeric canonical id (plastic)
        "mobile_phone 0.3 0.3 0.1 0.1\n" # named source class -> ewaste
        "food 0.6 0.6 0.1 0.1\n"         # discarded
        "6 1.5 0.5 0.2 0.2\n"            # invalid bbox -> dropped
        "bad line\n"
    )
    _img(tmp_path / "images" / "b.jpg", colour=(200, 0, 0))
    (tmp_path / "labels" / "b.txt").write_text("food 0.5 0.5 0.2 0.2\n")  # nothing left -> image skipped
    _img(tmp_path / "images" / "c.jpg", colour=(0, 200, 0))                  # no label file -> skipped
    records = prep.parse_yolo(tmp_path, mappings, discard, names)
    assert [r.image_path.name for r in records] == ["a.jpg"]
    assert [a.class_id for a in records[0].annotations] == [2, names.index("ewaste")]


def test_parse_yolo_skips_byte_identical_duplicates(tmp_path, cfg) -> None:
    mappings, discard, names = cfg
    for n in ("x", "y"):
        _img(tmp_path / f"{n}.jpg")
        (tmp_path / f"{n}.txt").write_text("0 0.5 0.5 0.5 0.5")
    assert len(prep.parse_yolo(tmp_path, mappings, discard, names)) == 1


def _coco(images, annotations, categories):
    return {"images": images, "annotations": annotations, "categories": categories}


def test_parse_coco_per_split_layout(tmp_path, cfg) -> None:
    """Roboflow-style export: train/_annotations.coco.json with file_name relative to train/."""
    mappings, discard, names = cfg
    split = tmp_path / "train"
    _img(split / "img1.jpg", size=(200, 100))
    (split / "_annotations.coco.json").write_text(json.dumps(_coco(
        images=[{"id": 1, "file_name": "img1.jpg", "width": 200, "height": 100},
                {"id": 2, "file_name": "missing.jpg", "width": 10, "height": 10}],
        annotations=[{"id": 1, "image_id": 1, "category_id": 1, "bbox": [50, 25, 100, 50]},
                     {"id": 2, "image_id": 1, "category_id": 0, "bbox": [0, 0, 10, 10]},
                     {"id": 3, "image_id": 1, "category_id": 2, "bbox": [0, 0, 10, 10]}],
        categories=[{"id": 0, "name": "biodegradable"}, {"id": 1, "name": "Cardboard"},
                    {"id": 2, "name": "metal"}],
    )))
    records = prep.parse_coco(tmp_path, mappings, discard, names)
    assert len(records) == 1
    anns = records[0].annotations
    assert [a.class_id for a in anns] == [names.index("cardboard"), names.index("metal")]
    a = anns[0]
    assert (a.cx, a.cy, a.w, a.h) == pytest.approx((0.5, 0.5, 0.5, 0.5))


def test_parse_pascal_voc(tmp_path, cfg) -> None:
    mappings, discard, names = cfg
    _img(tmp_path / "bat.jpg", size=(100, 200))
    (tmp_path / "bat.xml").write_text(
        "<annotation><filename>bat.jpg</filename><size><width>100</width><height>200</height></size>"
        "<object><name>Batteries</name><bndbox><xmin>10</xmin><ymin>20</ymin><xmax>60</xmax><ymax>120</ymax></bndbox></object>"
        "<object><name>food</name><bndbox><xmin>0</xmin><ymin>0</ymin><xmax>5</xmax><ymax>5</ymax></bndbox></object>"
        "</annotation>"
    )
    (tmp_path / "broken.xml").write_text("<annotation><unclosed>")
    records = prep.parse_pascal_voc(tmp_path, mappings, discard, names)
    assert len(records) == 1
    (a,) = records[0].annotations
    assert a.class_id == names.index("battery")
    assert (a.cx, a.cy, a.w, a.h) == pytest.approx((0.35, 0.35, 0.5, 0.5))


# ─────────────────────────────────────────────────────────────────────────────
# split / write / stats / validate
# ─────────────────────────────────────────────────────────────────────────────

def _records(n):
    return [prep.ImageRecord(Path(f"img{i}.jpg"), [prep.Annotation(i % 7, 0.5, 0.5, 0.2, 0.2)]) for i in range(n)]


def test_split_is_deterministic_disjoint_and_complete() -> None:
    recs = _records(100)
    a = prep.split_records(recs, 0.8, 0.1, seed=42)
    b = prep.split_records(recs, 0.8, 0.1, seed=42)
    assert a == b
    train, val_, test = a
    assert (len(train), len(val_), len(test)) == (80, 10, 10)
    names = [r.image_path for r in train + val_ + test]
    assert len(set(names)) == 100
    assert prep.split_records(recs, 0.8, 0.1, seed=7)[0] != train


def test_build_stats_counts() -> None:
    names = ["paper", "cardboard", "plastic", "metal", "glass", "ewaste", "battery"]
    train, val_, test = _records(14), _records(7), []
    stats = prep.build_stats(names, train, val_, test)
    assert stats["total_images"] == 21 and stats["total_objects"] == 21
    assert stats["objects_per_class"]["paper"] == 3
    assert stats["train_images"] == 14 and stats["test_images"] == 0


def test_write_split_then_validate_roundtrip(tmp_path, cfg) -> None:
    mappings, discard, names = cfg
    src = tmp_path / "src"
    recs = []
    for i in range(7):
        p = _img(src / f"im{i}.jpg", colour=(i * 30, 0, 0))
        recs.append(prep.ImageRecord(p, [prep.Annotation(i, 0.5, 0.5, 0.25, 0.25)]))
    out = tmp_path / "data"
    prep.write_split(recs, out, "train")
    prep.write_split(recs[:2], out, "val")
    lines = (out / "train" / "labels" / "im3.txt").read_text().split()
    assert lines == ["3", "0.500000", "0.500000", "0.250000", "0.250000"]
    n_img, n_obj, classes, errors = val.validate_split(out / "train")
    assert (n_img, n_obj, classes, errors) == (7, 7, set(range(7)), [])


def test_validate_split_reports_every_error_kind(tmp_path) -> None:
    split = tmp_path / "train"
    (split / "labels").mkdir(parents=True)
    _img(split / "images" / "ok.jpg")
    _img(split / "images" / "nolabel.jpg")
    _img(split / "images" / "empty.jpg")
    (split / "labels" / "ok.txt").write_text("0 0.5 0.5 0.2 0.2\n9 0.5 0.5 0.2 0.2\n1 0.5 0.5 0 0.2\n2 0.5\nx y z w v\n")
    (split / "labels" / "orphan.txt").write_text("0 0.5 0.5 0.2 0.2")
    (split / "labels" / "empty.txt").write_text("")
    n_img, n_obj, classes, errors = val.validate_split(split)
    text = "\n".join(errors)
    assert n_img == 3 and n_obj == 1 and classes == {0}
    for needle in ("out of range", "not in (0,1]", "expected 5 fields", "cannot parse",
                   "Label without image", "Image without label file: nolabel",
                   "0 valid annotations: empty"):
        assert needle in text, needle


def test_validate_split_missing_dirs(tmp_path) -> None:
    _, _, _, errors = val.validate_split(tmp_path / "nope")
    assert errors and "Missing directory" in errors[0]
