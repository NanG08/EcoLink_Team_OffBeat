"""
Post-split dataset validator for the EcoLink vision pipeline.

Scans ml/vision/data/{train,val,test} and checks:
- Every label file has a corresponding image
- Every image has at least one annotation
- All class IDs are in [0, 6]
- All bounding box values are in (0, 1]
- No image filename appears in more than one split
- Prints per-split summary table

Usage:
    python ml/vision/scripts/validate_dataset.py [--data-dir PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_NC = 7  # number of canonical classes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the EcoLink vision dataset splits.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("ml/vision/data"),
        help="Root data directory containing train/, val/, test/ subdirectories",
    )
    return parser.parse_args()


def validate_split(split_dir: Path) -> tuple[int, int, set[int], list[str]]:
    """
    Validate one split directory.

    Returns:
        (image_count, object_count, classes_present, error_messages)
    """
    errors: list[str] = []
    images_dir = split_dir / "images"
    labels_dir = split_dir / "labels"

    if not images_dir.exists():
        errors.append(f"Missing directory: {images_dir}")
        return 0, 0, set(), errors
    if not labels_dir.exists():
        errors.append(f"Missing directory: {labels_dir}")
        return 0, 0, set(), errors

    # Collect image stems
    image_stems: set[str] = set()
    for ext in _IMAGE_EXTS:
        for img in images_dir.glob(f"*{ext}"):
            image_stems.add(img.stem)
        for img in images_dir.glob(f"*{ext.upper()}"):
            image_stems.add(img.stem)

    image_count = len(image_stems)
    object_count = 0
    classes_present: set[int] = set()

    # Check every label file
    label_files = list(labels_dir.glob("*.txt"))
    for lf in label_files:
        if lf.stem not in image_stems:
            errors.append(f"Label without image: {lf}")
            continue

        lines = lf.read_text(encoding="utf-8").splitlines()
        valid_lines = 0
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 5:
                errors.append(f"{lf.name} line {lineno}: expected 5 fields, got {len(parts)}")
                continue
            try:
                cid = int(parts[0])
                cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            except ValueError:
                errors.append(f"{lf.name} line {lineno}: cannot parse values")
                continue
            if cid < 0 or cid >= _NC:
                errors.append(f"{lf.name} line {lineno}: class_id {cid} out of range [0,{_NC-1}]")
                continue
            if not (0.0 < cx <= 1.0 and 0.0 < cy <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
                errors.append(
                    f"{lf.name} line {lineno}: bbox values not in (0,1] "
                    f"(cx={cx:.4f} cy={cy:.4f} w={w:.4f} h={h:.4f})"
                )
                continue
            valid_lines += 1
            classes_present.add(cid)
            object_count += 1

        if valid_lines == 0:
            errors.append(f"Image with 0 valid annotations: {lf.stem}")

    # Check images without any label file
    label_stems = {lf.stem for lf in label_files}
    for stem in image_stems:
        if stem not in label_stems:
            errors.append(f"Image without label file: {stem}")

    return image_count, object_count, classes_present, errors


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir

    if not data_dir.exists():
        print(f"ERROR: data directory not found: {data_dir}")
        sys.exit(1)

    splits = ["train", "val", "test"]
    all_errors: list[str] = []
    split_results: dict[str, tuple] = {}

    # Track all stems across splits to detect cross-split duplicates
    all_stems: dict[str, str] = {}   # stem → split name
    cross_split_errors: list[str] = []

    for split in splits:
        split_dir = data_dir / split
        if not split_dir.exists():
            print(f"WARNING: split directory not found: {split_dir}")
            split_results[split] = (0, 0, set(), [f"Directory not found: {split_dir}"])
            continue

        img_count, obj_count, classes, errors = validate_split(split_dir)
        split_results[split] = (img_count, obj_count, classes, errors)
        all_errors.extend(errors)

        # Check for class distribution (warning only)
        missing_classes = set(range(_NC)) - classes
        if missing_classes and img_count > 0:
            print(f"WARNING: Split '{split}' missing classes: {sorted(missing_classes)}")

        # Cross-split duplicate check
        images_dir = split_dir / "images"
        if images_dir.exists():
            for ext in _IMAGE_EXTS:
                for img in images_dir.glob(f"*{ext}"):
                    if img.stem in all_stems:
                        cross_split_errors.append(
                            f"Duplicate across splits: '{img.stem}' in both "
                            f"'{all_stems[img.stem]}' and '{split}'"
                        )
                    else:
                        all_stems[img.stem] = split

    all_errors.extend(cross_split_errors)

    # Print summary table
    print("\n" + "=" * 60)
    print(f"{'Split':<8} {'Images':>8} {'Objects':>10} {'Classes':>10}")
    print("-" * 60)
    for split in splits:
        img, obj, classes, _ = split_results.get(split, (0, 0, set(), []))
        cls_str = str(sorted(classes)) if classes else "none"
        print(f"{split:<8} {img:>8} {obj:>10} {cls_str:>10}")
    print("=" * 60)

    if all_errors:
        print(f"\nERRORS FOUND ({len(all_errors)} total):")
        for err in all_errors[:50]:   # cap output
            print(f"  ✗ {err}")
        if len(all_errors) > 50:
            print(f"  ... and {len(all_errors) - 50} more")
        sys.exit(1)
    else:
        print("\nOK — dataset validation passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
