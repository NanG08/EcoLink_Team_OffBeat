"""
Generate bounding boxes for classification-only battery photos and write them
as Pascal VOC XML next to each image (input for prepare_dataset.py --format pascal-voc).

The upstream battery images are mostly product shots on plain backgrounds, so
two labelers are combined:

1. Background subtraction — the border colour is treated as background and the
   bounding box of everything that differs from it is the object.  Trusted only
   when the border is uniform (clean product shot).
2. YOLO-World (open-vocabulary detector) prompted with battery phrases — used
   for cluttered backgrounds, and to split clean shots that hold several cells.

Images where neither labeler is confident are skipped (no XML written).
These are machine-generated labels; spot-check them with --sheet before training.

Usage (from repo root):
    python ml/vision/scripts/autolabel_batteries.py \\
        --images ml/vision/data/raw/battery_images \\
        --world-weights yolov8s-worldv2.pt --sheet battery_labels.jpg
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

_PROMPTS = [
    "battery", "AA battery", "battery cell", "button cell battery", "phone battery",
    "laptop battery", "car battery", "9V battery", "lithium battery pack",
]


def background_box(rgb: np.ndarray) -> tuple[tuple[int, int, int, int] | None, bool]:
    """Return (xyxy box of the foreground, border_is_uniform)."""
    import cv2

    h, w = rgb.shape[:2]
    b = max(2, int(0.03 * min(h, w)))
    border = np.concatenate([
        rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
        rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3),
    ]).astype(np.int16)
    bg = np.median(border, axis=0)
    uniform = float((np.abs(border - bg).max(axis=1) < 28).mean()) > 0.92

    diff = np.abs(rgb.astype(np.int16) - bg).max(axis=2)
    mask = (diff > 40).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    comps = [s for s in stats[1:] if s[4] >= 0.005 * h * w]
    if not comps:
        return None, uniform
    x1 = min(s[0] for s in comps)
    y1 = min(s[1] for s in comps)
    x2 = max(s[0] + s[2] for s in comps)
    y2 = max(s[1] + s[3] for s in comps)
    frac = (x2 - x1) * (y2 - y1) / (h * w)
    if not 0.02 <= frac <= 0.98:
        return None, uniform
    return (int(x1), int(y1), int(x2), int(y2)), uniform


def _inside(inner, outer, slack: int = 8) -> bool:
    return (inner[0] >= outer[0] - slack and inner[1] >= outer[1] - slack
            and inner[2] <= outer[2] + slack and inner[3] <= outer[3] + slack)


def choose_boxes(bg_box, uniform: bool, world_boxes: list[tuple], min_conf: float) -> tuple[list, str]:
    """Combine both labelers; returns (boxes_xyxy, source_tag)."""
    confident = [b for b, c in world_boxes if c >= min_conf]
    if uniform and bg_box is not None:
        parts = [b for b in confident if _inside(b, bg_box)]
        if len(parts) >= 2:
            return parts, "world-split"
        return [bg_box], "background"
    if confident:
        return confident, "world"
    return [], "skipped"


def write_voc(img_path: Path, size: tuple[int, int], boxes: list, label: str) -> Path:
    w, h = size
    root = ET.Element("annotation")
    ET.SubElement(root, "folder").text = img_path.parent.name
    ET.SubElement(root, "filename").text = img_path.name
    sz = ET.SubElement(root, "size")
    ET.SubElement(sz, "width").text = str(w)
    ET.SubElement(sz, "height").text = str(h)
    ET.SubElement(sz, "depth").text = "3"
    for x1, y1, x2, y2 in boxes:
        obj = ET.SubElement(root, "object")
        ET.SubElement(obj, "name").text = label
        bb = ET.SubElement(obj, "bndbox")
        for k, v in zip(("xmin", "ymin", "xmax", "ymax"), (x1, y1, x2, y2)):
            ET.SubElement(bb, k).text = str(int(round(v)))
    out = img_path.with_suffix(".xml")
    ET.ElementTree(root).write(out, encoding="utf-8")
    return out


def contact_sheet(rows: list[tuple[Path, list, str]], out: Path, n: int = 48, seed: int = 0) -> None:
    from PIL import ImageDraw

    colours = {"background": "lime", "world": "orange", "world-split": "cyan"}
    rows = list(rows)
    random.Random(seed).shuffle(rows)
    tile = 240
    cols = 8
    rows = rows[:n]
    sheet = Image.new("RGB", (tile * cols, tile * ((len(rows) + cols - 1) // cols)), "white")
    for i, (p, boxes, tag) in enumerate(rows):
        im = Image.open(p).convert("RGB")
        sx, sy = tile / im.width, tile / im.height
        im = im.resize((tile, tile))
        d = ImageDraw.Draw(im)
        for x1, y1, x2, y2 in boxes:
            d.rectangle([x1 * sx, y1 * sy, x2 * sx, y2 * sy], outline=colours.get(tag, "red"), width=3)
        d.text((4, 4), tag, fill="red")
        sheet.paste(im, ((i % cols) * tile, (i // cols) * tile))
    sheet.save(out, quality=85)
    log.info("Contact sheet written to %s", out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--images", type=Path, default=Path("ml/vision/data/raw/battery_images"))
    p.add_argument("--world-weights", type=str, default="yolov8s-worldv2.pt")
    p.add_argument("--label", type=str, default="battery", help="Class name written to the XML")
    p.add_argument("--min-conf", type=float, default=0.15, help="YOLO-World confidence to accept a box")
    p.add_argument("--min-area", type=float, default=0.03,
                   help="Drop images whose boxes cover less than this fraction (usually partial boxes)")
    p.add_argument("--sheet", type=Path, default=None, help="Optional contact-sheet JPEG for spot checks")
    args = p.parse_args()

    from ultralytics import YOLOWorld

    world = YOLOWorld(args.world_weights)
    world.set_classes(_PROMPTS)

    images = sorted(x for x in args.images.iterdir() if x.suffix.lower() in {".jpg", ".jpeg", ".png"})
    counts: dict[str, int] = {}
    labelled: list[tuple[Path, list, str]] = []
    for img_path in images:
        rgb = np.asarray(Image.open(img_path).convert("RGB"))
        bg, uniform = background_box(rgb)
        bgr = np.ascontiguousarray(rgb[..., ::-1])
        res = world.predict(bgr, conf=0.05, iou=0.5, agnostic_nms=True, verbose=False)[0]
        world_boxes = [(tuple(b), float(c)) for b, c in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist())]
        boxes, tag = choose_boxes(bg, uniform, world_boxes, args.min_conf)
        covered = sum((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in boxes) / (rgb.shape[0] * rgb.shape[1])
        if boxes and covered < args.min_area:
            boxes, tag = [], "skipped-small"
        counts[tag] = counts.get(tag, 0) + 1
        xml_path = img_path.with_suffix(".xml")
        if boxes:
            write_voc(img_path, (rgb.shape[1], rgb.shape[0]), boxes, args.label)
            labelled.append((img_path, boxes, tag))
        elif xml_path.exists():
            xml_path.unlink()
    log.info("Auto-labelled %d/%d images: %s", len(labelled), len(images), counts)
    if args.sheet:
        contact_sheet(labelled, args.sheet)


if __name__ == "__main__":
    main()
