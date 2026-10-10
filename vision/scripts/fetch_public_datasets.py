"""
Download public waste datasets into ml/vision/data/raw/ in formats that
prepare_dataset.py understands.

Sources (one sub-directory each under data/raw/):

    garbage_coco/   keremberke/garbage-object-detection (Roboflow, CC BY 4.0)
                    paper, cardboard, plastic, metal, glass (+ biodegradable,
                    which dataset_mapping.yaml discards).  COCO format.
    openimages_ewaste/
                    Open Images V7 validation+test boxes for consumer
                    electronics (CC BY 4.0 annotations, CC BY 2.0 images).
                    Written as YOLO .txt files whose class field is a *name*
                    (e.g. "mobile_phone"), so dataset_mapping.yaml resolves it.
    battery_images/ khoaliamle/Garbage_Classification_YOLO "battery" images
                    (MIT).  Classification-only upstream; boxes are produced
                    by autolabel_batteries.py and written as Pascal VOC XML.

Usage (from repo root):
    python ml/vision/scripts/fetch_public_datasets.py --all
    python ml/vision/scripts/fetch_public_datasets.py --garbage --max-garbage 3000
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import random
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

_VISION_DIR = Path(__file__).resolve().parent.parent
_RAW_DIR = _VISION_DIR / "data" / "raw"

_HF = "https://huggingface.co/datasets"
_GARBAGE_REPO = "keremberke/garbage-object-detection"
_BATTERY_REPO = "khoaliamle/Garbage_Classification_YOLO"
_OI_BOXES = {
    "validation": "https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv",
    "test": "https://storage.googleapis.com/openimages/v5/test-annotations-bbox.csv",
}
_OI_CLASSES_CSV = "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv"
_OI_IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/{split}/{image_id}.jpg"

# Open Images display name -> source class name understood by dataset_mapping.yaml
_OI_EWASTE = {
    "Mobile phone": "mobile_phone",
    "Laptop": "laptop",
    "Computer keyboard": "keyboard",
    "Computer mouse": "mouse",
    "Computer monitor": "monitor",
    "Television": "television",
    "Remote control": "remote_control",
    "Tablet computer": "tablet",
    "Headphones": "headphones",
    "Camera": "camera",
    "Printer": "printer",
}
# Images that also contain these are skipped: they hold unlabelled objects
# that belong to one of our *other* classes and would teach "background".
_OI_CONFLICTS = {"Bottle", "Tin can", "Plastic bag", "Paper towel", "Toilet paper", "Box"}

_USER_AGENT = {"User-Agent": "EcoLink-dataset-fetch/1.0"}


def _download(url: str, dest: Path, timeout: int = 120) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=_USER_AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:  # noqa: S310
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    tmp.replace(dest)
    return dest


def _save_resized(data: bytes, dest: Path, max_side: int) -> tuple[int, int]:
    """Re-encode *data* as RGB JPEG with its longest side <= *max_side*."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((max_side, max_side))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=90)
    return img.size


# ─────────────────────────────────────────────────────────────────────────────
# keremberke/garbage-object-detection  (COCO)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_garbage(max_images: int, seed: int) -> None:
    out = _RAW_DIR / "garbage_coco"
    zips = _RAW_DIR / "_downloads" / "garbage"
    for split in ("train", "valid", "test"):
        zpath = _download(f"{_HF}/{_GARBAGE_REPO}/resolve/main/data/{split}.zip", zips / f"{split}.zip")
        split_dir = out / split
        if not (split_dir / "annotations.json").exists():
            log.info("Extracting %s", zpath.name)
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(split_dir)
            # Roboflow names the file _annotations.coco.json
            for ann in split_dir.rglob("_annotations.coco.json"):
                ann.replace(split_dir / "annotations.json")

    # Optional deterministic subsample (keeps CPU training tractable)
    if max_images > 0:
        rng = random.Random(seed)
        ann_paths = sorted(out.glob("*/annotations.json"))
        totals = {p: len(json.loads(p.read_text(encoding="utf-8"))["images"]) for p in ann_paths}
        grand = sum(totals.values())
        if grand > max_images:
            for p in ann_paths:
                coco = json.loads(p.read_text(encoding="utf-8"))
                keep_n = max(1, round(max_images * totals[p] / grand))
                images = sorted(coco["images"], key=lambda im: im["id"])
                rng.shuffle(images)
                kept = images[:keep_n]
                kept_ids = {im["id"] for im in kept}
                coco["images"] = kept
                coco["annotations"] = [a for a in coco["annotations"] if a["image_id"] in kept_ids]
                p.write_text(json.dumps(coco), encoding="utf-8")
                log.info("Subsampled %s: %d -> %d images", p.parent.name, totals[p], len(kept))
    log.info("garbage_coco ready at %s", out)


# ─────────────────────────────────────────────────────────────────────────────
# Open Images e-waste  (YOLO txt with class *names*)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_openimages_ewaste(max_images: int, max_side: int, seed: int, workers: int) -> None:
    out = _RAW_DIR / "openimages_ewaste"
    dl = _RAW_DIR / "_downloads" / "openimages"
    classes_csv = _download(_OI_CLASSES_CSV, dl / "classes.csv")
    mid_to_name = {}
    with open(classes_csv, encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2:
                mid_to_name[row[0]] = row[1]

    # image -> list of (split, name, xmin, xmax, ymin, ymax), plus conflict flags
    boxes: dict[str, list] = {}
    conflicts: set[str] = set()
    split_of: dict[str, str] = {}
    for split, url in _OI_BOXES.items():
        path = _download(url, dl / f"{split}-annotations-bbox.csv")
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name = mid_to_name.get(row["LabelName"], "")
                image_id = row["ImageID"]
                if name in _OI_CONFLICTS:
                    conflicts.add(image_id)
                if name not in _OI_EWASTE or row.get("IsDepiction") == "1":
                    continue
                split_of[image_id] = split
                boxes.setdefault(image_id, []).append((
                    _OI_EWASTE[name],
                    float(row["XMin"]), float(row["XMax"]), float(row["YMin"]), float(row["YMax"]),
                ))

    candidates = sorted(i for i in boxes if i not in conflicts)
    rng = random.Random(seed)
    rng.shuffle(candidates)
    # Round-robin over the dominant class so phones/laptops don't crowd out the rest
    by_class: dict[str, list[str]] = {}
    for image_id in candidates:
        by_class.setdefault(boxes[image_id][0][0], []).append(image_id)
    selected: list[str] = []
    while len(selected) < max_images and any(by_class.values()):
        for cls in sorted(by_class):
            if by_class[cls] and len(selected) < max_images:
                selected.append(by_class[cls].pop())
    log.info("Open Images: %d e-waste candidates, %d selected", len(candidates), len(selected))

    img_dir, lbl_dir = out / "images", out / "labels"
    lbl_dir.mkdir(parents=True, exist_ok=True)

    def work(image_id: str) -> bool:
        dest = img_dir / f"oi_{image_id}.jpg"
        if not dest.exists():
            url = _OI_IMAGE_URL.format(split=split_of[image_id], image_id=image_id)
            try:
                req = urllib.request.Request(url, headers=_USER_AGENT)
                with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                    _save_resized(resp.read(), dest, max_side)
            except Exception as exc:  # noqa: BLE001
                log.warning("skip %s: %s", image_id, exc)
                return False
        lines = []
        for name, x0, x1, y0, y1 in boxes[image_id]:
            cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
            if w > 0 and h > 0:
                lines.append(f"{name} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
        (lbl_dir / f"oi_{image_id}.txt").write_text("\n".join(lines), encoding="utf-8")
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ok = sum(pool.map(work, selected))
    log.info("openimages_ewaste ready at %s (%d images)", out, ok)


# ─────────────────────────────────────────────────────────────────────────────
# Battery images  (boxes added later by autolabel_batteries.py)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_battery_images(max_side: int, workers: int) -> None:
    out = _RAW_DIR / "battery_images"
    files: list[str] = []
    for split in ("train", "val", "test"):
        api = f"https://huggingface.co/api/datasets/{_BATTERY_REPO}/tree/main/classify/{split}/battery"
        with urllib.request.urlopen(urllib.request.Request(api, headers=_USER_AGENT), timeout=60) as r:  # noqa: S310
            files += [e["path"] for e in json.load(r) if e["type"] == "file"]
    log.info("Battery: %d images listed", len(files))

    def work(rel: str) -> bool:
        dest = out / f"bat_{Path(rel).parent.parent.name}_{Path(rel).stem}.jpg"
        if dest.exists():
            return True
        try:
            req = urllib.request.Request(f"{_HF}/{_BATTERY_REPO}/resolve/main/{rel}", headers=_USER_AGENT)
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                _save_resized(resp.read(), dest, max_side)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("skip %s: %s", rel, exc)
            return False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ok = sum(pool.map(work, files))
    log.info("battery_images ready at %s (%d images)", out, ok)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--all", action="store_true", help="Fetch every source")
    p.add_argument("--garbage", action="store_true")
    p.add_argument("--openimages", action="store_true")
    p.add_argument("--battery", action="store_true")
    p.add_argument("--max-garbage", type=int, default=0, help="Subsample garbage_coco to N images (0 = all)")
    p.add_argument("--max-ewaste", type=int, default=800)
    p.add_argument("--max-side", type=int, default=640, help="Resize downloaded photos to this longest side")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.all or args.garbage:
        fetch_garbage(args.max_garbage, args.seed)
    if args.all or args.openimages:
        fetch_openimages_ewaste(args.max_ewaste, args.max_side, args.seed, args.workers)
    if args.all or args.battery:
        fetch_battery_images(args.max_side, args.workers)


if __name__ == "__main__":
    main()
