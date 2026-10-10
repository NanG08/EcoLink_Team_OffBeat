"""
Dataset preparation and normalisation script for the EcoLink vision pipeline.

Reads raw annotated waste images, maps source class names to EcoLink's
7 canonical classes, converts to YOLO format, and writes train/val/test splits.

Usage:
    python ml/vision/scripts/prepare_dataset.py --help
    python ml/vision/scripts/prepare_dataset.py \\
        --source-dir ml/vision/data/raw \\
        --output-dir ml/vision/data \\
        --format yolo

Supported input formats: yolo, coco, pascal-voc
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class Annotation(NamedTuple):
    class_id: int   # EcoLink canonical class ID
    cx: float
    cy: float
    w: float
    h: float


class ImageRecord(NamedTuple):
    image_path: Path
    annotations: list[Annotation]


# ─────────────────────────────────────────────────────────────────────────────
# Config loading
# ─────────────────────────────────────────────────────────────────────────────

def load_mapping(mapping_path: Path) -> tuple[dict[str, str | None], list[str]]:
    """Return (mappings dict, discard list) from dataset_mapping.yaml."""
    with open(mapping_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    mappings: dict[str, str | None] = data.get("mappings", {})
    discard: list[str] = data.get("discard", [])
    return mappings, discard


def load_classes(classes_path: Path) -> list[str]:
    """Return ordered list of canonical class names from classes.yaml."""
    with open(classes_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["class_names"]


# ─────────────────────────────────────────────────────────────────────────────
# Source class resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_label(
    source: str,
    mappings: dict[str, str | None],
    discard: list[str],
    class_names: list[str],
) -> int | None:
    """
    Map *source* class name to an EcoLink canonical class ID.

    Returns None if the class should be discarded.
    """
    source_lower = source.lower().strip()
    if source_lower in discard:
        return None
    canonical = mappings.get(source_lower)
    if canonical is None:
        # Try direct match in canonical class names
        if source_lower in class_names:
            return class_names.index(source_lower)
        return None   # discard unmapped
    if canonical not in class_names:
        log.warning("Mapping for '%s' → '%s' is not a canonical class; discarding", source, canonical)
        return None
    return class_names.index(canonical)


def resolve_id(
    class_id: int,
    mappings: dict[str, str | None],
    discard: list[str],
    class_names: list[str],
    source_classes: list[str] | None = None,
) -> int | None:
    """
    Map a numeric source *class_id* to a canonical class ID.

    If *source_classes* is provided (list of class names in source dataset order),
    the ID is first translated to a name and then resolved through the mappings.
    Otherwise, if the ID is in range [0, 6], it is used directly.
    """
    if source_classes is not None:
        if class_id < 0 or class_id >= len(source_classes):
            return None
        return resolve_label(source_classes[class_id], mappings, discard, class_names)
    # No source class list — treat ID as already canonical if in range
    if 0 <= class_id < len(class_names):
        return class_id
    return None


# ─────────────────────────────────────────────────────────────────────────────
# BBox validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_yolo_bbox(cx: float, cy: float, w: float, h: float) -> bool:
    """Return True if YOLO bbox values are valid (normalised, non-zero)."""
    return (0.0 < cx <= 1.0 and 0.0 < cy <= 1.0 and
            0.0 < w <= 1.0 and 0.0 < h <= 1.0)


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


# ─────────────────────────────────────────────────────────────────────────────
# Image duplicate detection
# ─────────────────────────────────────────────────────────────────────────────

def image_hash(path: Path) -> str:
    """Return SHA-256 hex digest of the first 64 KB of *path* (fast approximation)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read(65536))
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Format-specific parsers
# ─────────────────────────────────────────────────────────────────────────────

def parse_yolo(
    source_dir: Path,
    mappings: dict[str, str | None],
    discard: list[str],
    class_names: list[str],
) -> list[ImageRecord]:
    """Parse YOLO-format dataset (images + .txt label files)."""
    records: list[ImageRecord] = []
    seen_hashes: dict[str, Path] = {}

    # Discover all image files recursively
    image_paths: list[Path] = []
    for ext in _IMAGE_EXTS:
        image_paths.extend(source_dir.rglob(f"*{ext}"))
        image_paths.extend(source_dir.rglob(f"*{ext.upper()}"))

    for img_path in sorted(set(image_paths)):
        # Duplicate detection
        try:
            h = image_hash(img_path)
            if h in seen_hashes:
                log.warning("Duplicate image detected: %s (same as %s); skipping",
                            img_path, seen_hashes[h])
                continue
            seen_hashes[h] = img_path
        except OSError as exc:
            log.warning("Could not read %s: %s", img_path, exc)
            continue

        # Find label file: same stem .txt in same dir or sibling labels/ dir
        label_path = img_path.with_suffix(".txt")
        if not label_path.exists():
            # Try sibling labels/ directory
            candidate = img_path.parent.parent / "labels" / img_path.with_suffix(".txt").name
            if candidate.exists():
                label_path = candidate
            else:
                log.warning("No label file for image %s; skipping", img_path)
                continue

        annotations = _parse_yolo_txt(label_path, mappings, discard, class_names)
        if not annotations:
            log.warning("Image %s has 0 valid annotations after mapping; skipping", img_path)
            continue

        records.append(ImageRecord(image_path=img_path, annotations=annotations))

    return records


def _parse_yolo_txt(
    label_path: Path,
    mappings: dict[str, str | None],
    discard: list[str],
    class_names: list[str],
) -> list[Annotation]:
    annotations: list[Annotation] = []
    try:
        lines = label_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log.warning("Could not read label file %s: %s", label_path, exc)
        return annotations

    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            log.warning("%s line %d: expected 5 fields, got %d; skipping",
                        label_path.name, lineno, len(parts))
            continue
        try:
            raw_cls = parts[0]
            cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
        except ValueError:
            log.warning("%s line %d: cannot parse floats; skipping", label_path.name, lineno)
            continue

        # Resolve class (may be numeric ID or class name)
        try:
            src_id = int(raw_cls)
            canon_id = resolve_id(src_id, mappings, discard, class_names)
        except ValueError:
            canon_id = resolve_label(raw_cls, mappings, discard, class_names)

        if canon_id is None:
            continue  # discarded class

        if not validate_yolo_bbox(cx, cy, w, h):
            log.warning("%s line %d: invalid bbox (%.4f %.4f %.4f %.4f); skipping",
                        label_path.name, lineno, cx, cy, w, h)
            continue

        annotations.append(Annotation(class_id=canon_id, cx=cx, cy=cy, w=w, h=h))

    return annotations


def parse_coco(
    source_dir: Path,
    mappings: dict[str, str | None],
    discard: list[str],
    class_names: list[str],
) -> list[ImageRecord]:
    """Parse COCO-format dataset (instances_*.json)."""
    import glob

    records: list[ImageRecord] = []
    json_files = (list(source_dir.rglob("instances_*.json"))
                  + list(source_dir.rglob("annotations.json"))
                  + list(source_dir.rglob("_annotations.coco.json")))   # Roboflow export name
    if not json_files:
        log.warning("No COCO annotation JSON files found in %s", source_dir)
        return records

    for json_path in json_files:
        log.info("Parsing COCO annotation file: %s", json_path)
        with open(json_path, encoding="utf-8") as fh:
            coco = json.load(fh)

        # Build category id → EcoLink class id mapping
        cat_map: dict[int, int | None] = {}
        for cat in coco.get("categories", []):
            cat_id = cat["id"]
            cat_name = cat["name"].lower()
            canon_id = resolve_label(cat_name, mappings, discard, class_names)
            cat_map[cat_id] = canon_id

        # Build image id → image info mapping
        img_info: dict[int, dict] = {img["id"]: img for img in coco.get("images", [])}

        # Group annotations by image id
        anns_by_img: dict[int, list] = {}
        for ann in coco.get("annotations", []):
            img_id = ann["image_id"]
            anns_by_img.setdefault(img_id, []).append(ann)

        for img_id, img_meta in img_info.items():
            # file_name is relative to the annotation file's folder (per-split
            # exports such as train/_annotations.coco.json); fall back to source_dir.
            img_path = json_path.parent / img_meta["file_name"]
            if not img_path.exists():
                img_path = source_dir / img_meta["file_name"]
            if not img_path.exists():
                log.warning("Missing image: %s", img_path)
                continue

            W, H = float(img_meta.get("width", 1)), float(img_meta.get("height", 1))
            annotations: list[Annotation] = []

            for ann in anns_by_img.get(img_id, []):
                cat_id = ann["category_id"]
                canon_id = cat_map.get(cat_id)
                if canon_id is None:
                    continue

                # COCO bbox: [x_min, y_min, width, height]
                x, y, bw, bh = ann["bbox"]
                cx = (x + bw / 2) / W
                cy = (y + bh / 2) / H
                w = bw / W
                h = bh / H
                if not validate_yolo_bbox(cx, cy, w, h):
                    continue
                annotations.append(Annotation(class_id=canon_id, cx=cx, cy=cy, w=w, h=h))

            if annotations:
                records.append(ImageRecord(image_path=img_path, annotations=annotations))

    return records


def parse_pascal_voc(
    source_dir: Path,
    mappings: dict[str, str | None],
    discard: list[str],
    class_names: list[str],
) -> list[ImageRecord]:
    """Parse Pascal VOC XML annotations."""
    records: list[ImageRecord] = []

    for xml_path in sorted(source_dir.rglob("*.xml")):
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError as exc:
            log.warning("Could not parse XML %s: %s", xml_path, exc)
            continue

        root = tree.getroot()
        folder = root.findtext("folder") or ""
        filename = root.findtext("filename") or xml_path.stem

        # Find image file
        img_path: Path | None = None
        for ext in _IMAGE_EXTS:
            candidate = xml_path.parent / (filename if filename.lower().endswith(tuple(_IMAGE_EXTS)) else filename + ext)
            if candidate.exists():
                img_path = candidate
                break
        if img_path is None:
            log.warning("No image found for VOC annotation %s; skipping", xml_path)
            continue

        size_el = root.find("size")
        W = float(size_el.findtext("width") or "1") if size_el is not None else 1.0
        H = float(size_el.findtext("height") or "1") if size_el is not None else 1.0

        annotations: list[Annotation] = []
        for obj in root.findall("object"):
            name = (obj.findtext("name") or "").lower()
            canon_id = resolve_label(name, mappings, discard, class_names)
            if canon_id is None:
                continue

            bndbox = obj.find("bndbox")
            if bndbox is None:
                continue
            xmin = float(bndbox.findtext("xmin") or "0")
            ymin = float(bndbox.findtext("ymin") or "0")
            xmax = float(bndbox.findtext("xmax") or "1")
            ymax = float(bndbox.findtext("ymax") or "1")
            bw, bh = xmax - xmin, ymax - ymin
            cx = (xmin + bw / 2) / W
            cy = (ymin + bh / 2) / H
            w = bw / W
            h = bh / H
            if not validate_yolo_bbox(cx, cy, w, h):
                continue
            annotations.append(Annotation(class_id=canon_id, cx=cx, cy=cy, w=w, h=h))

        if annotations:
            records.append(ImageRecord(image_path=img_path, annotations=annotations))

    return records


# ─────────────────────────────────────────────────────────────────────────────
# Split and write
# ─────────────────────────────────────────────────────────────────────────────

def split_records(
    records: list[ImageRecord],
    train_ratio: float,
    val_ratio: float,
    seed: int,
) -> tuple[list[ImageRecord], list[ImageRecord], list[ImageRecord]]:
    """Deterministically shuffle and split into train/val/test."""
    rng = random.Random(seed)
    shuffled = list(records)
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    train = shuffled[:n_train]
    val = shuffled[n_train: n_train + n_val]
    test = shuffled[n_train + n_val:]
    return train, val, test


def write_split(
    records: list[ImageRecord],
    output_dir: Path,
    split_name: str,
) -> None:
    """Write images and YOLO .txt label files for one split."""
    images_dir = output_dir / split_name / "images"
    labels_dir = output_dir / split_name / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    for rec in records:
        dst_img = images_dir / rec.image_path.name
        shutil.copy2(rec.image_path, dst_img)

        label_lines = []
        for ann in rec.annotations:
            label_lines.append(
                f"{ann.class_id} {ann.cx:.6f} {ann.cy:.6f} {ann.w:.6f} {ann.h:.6f}"
            )
        (labels_dir / (rec.image_path.stem + ".txt")).write_text(
            "\n".join(label_lines), encoding="utf-8"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────────────────────────────

def build_stats(
    class_names: list[str],
    train: list[ImageRecord],
    val: list[ImageRecord],
    test: list[ImageRecord],
) -> dict:
    """Build a dataset statistics report."""
    def count_objects(records: list[ImageRecord]) -> dict[str, int]:
        counts: dict[str, int] = {name: 0 for name in class_names}
        for rec in records:
            for ann in rec.annotations:
                counts[class_names[ann.class_id]] += 1
        return counts

    def count_images_per_class(records: list[ImageRecord]) -> dict[str, int]:
        counts: dict[str, int] = {name: 0 for name in class_names}
        for rec in records:
            seen = set()
            for ann in rec.annotations:
                label = class_names[ann.class_id]
                if label not in seen:
                    counts[label] += 1
                    seen.add(label)
        return counts

    all_records = train + val + test
    train_objs = count_objects(train)
    val_objs = count_objects(val)
    test_objs = count_objects(test)
    all_objs = count_objects(all_records)
    total_objects = sum(all_objs.values())

    return {
        "total_images": len(all_records),
        "total_objects": total_objects,
        "objects_per_class": all_objs,
        "images_per_class": count_images_per_class(all_records),
        "train_images": len(train),
        "val_images": len(val),
        "test_images": len(test),
        "train_objects_per_class": train_objs,
        "val_objects_per_class": val_objs,
        "test_objects_per_class": test_objs,
    }


def print_stats(stats: dict, class_names: list[str]) -> None:
    print("\nDataset Statistics")
    print("==================")
    print(f"Total images:  {stats['total_images']}")
    print(f"Total objects: {stats['total_objects']}")
    print("\nObjects per class:")
    for name in class_names:
        n = stats["objects_per_class"].get(name, 0)
        print(f"  {name:<12} {n}")
    print("\nSplit breakdown:")
    print(f"  Train: {stats['train_images']} images, {sum(stats['train_objects_per_class'].values())} objects")
    print(f"  Val:   {stats['val_images']} images, {sum(stats['val_objects_per_class'].values())} objects")
    print(f"  Test:  {stats['test_images']} images, {sum(stats['test_objects_per_class'].values())} objects")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and normalise an annotated waste dataset for EcoLink YOLOv8n training.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source-dir", type=Path, required=False,
                        default=Path("ml/vision/data/raw"),
                        help="Path to raw dataset directory")
    parser.add_argument("--output-dir", type=Path, default=Path("ml/vision/data"),
                        help="Output directory for processed train/val/test splits")
    parser.add_argument("--mapping",    type=Path,
                        default=Path("ml/vision/config/dataset_mapping.yaml"),
                        help="Path to dataset_mapping.yaml")
    parser.add_argument("--classes",    type=Path,
                        default=Path("ml/vision/config/classes.yaml"),
                        help="Path to classes.yaml")
    parser.add_argument("--split",      type=float, nargs=3, default=[0.8, 0.1, 0.1],
                        metavar=("TRAIN", "VAL", "TEST"),
                        help="Train/val/test split ratios (must sum to 1.0)")
    parser.add_argument("--seed",       type=int, default=42,
                        help="Random seed for deterministic split")
    parser.add_argument("--format",     type=str, default="yolo",
                        choices=["yolo", "coco", "pascal-voc"],
                        help="Source annotation format")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    train_r, val_r, test_r = args.split
    total = train_r + val_r + test_r
    if abs(total - 1.0) > 1e-4:
        log.warning("Split ratios sum to %.4f (expected 1.0); normalising", total)
        train_r /= total
        val_r /= total
        test_r /= total

    log.info("Loading config from %s and %s", args.mapping, args.classes)
    mappings, discard = load_mapping(args.mapping)
    class_names = load_classes(args.classes)

    log.info("Parsing %s dataset from %s ...", args.format, args.source_dir)
    if args.format == "yolo":
        records = parse_yolo(args.source_dir, mappings, discard, class_names)
    elif args.format == "coco":
        records = parse_coco(args.source_dir, mappings, discard, class_names)
    else:
        records = parse_pascal_voc(args.source_dir, mappings, discard, class_names)

    if not records:
        log.error("No valid records found in %s with format '%s'", args.source_dir, args.format)
        sys.exit(1)

    log.info("Found %d valid images with annotations", len(records))

    train, val, test = split_records(records, train_r, val_r, args.seed)
    log.info("Split: train=%d  val=%d  test=%d", len(train), len(val), len(test))

    for split_name, split_records_list in [("train", train), ("val", val), ("test", test)]:
        log.info("Writing %s split (%d images) ...", split_name, len(split_records_list))
        write_split(split_records_list, args.output_dir, split_name)

    stats = build_stats(class_names, train, val, test)
    print_stats(stats, class_names)

    # Save statistics
    stats_path = Path("ml/vision/artifacts/dataset_stats.json")
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    log.info("Statistics saved to %s", stats_path)

    processed_stats = args.output_dir / "processed" / "dataset_report.json"
    processed_stats.parent.mkdir(parents=True, exist_ok=True)
    processed_stats.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    log.info("Dataset report saved to %s", processed_stats)


if __name__ == "__main__":
    main()
