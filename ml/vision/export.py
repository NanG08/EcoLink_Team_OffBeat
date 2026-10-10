"""
Export the trained EcoLink weights for deployment, then verify the export twice:

1. numerical parity  - both models get the SAME square-letterboxed input; outputs
                       must match (this is what proves the export is correct)
2. pipeline parity   - both models go through the production run_inference().
                       ultralytics pads a .pt model's input to a stride-aligned
                       rectangle but a fixed-size export to a full square, so
                       near-threshold detections can differ; this measures how
                       much switching the deployed model would change answers.

Usage (from repo root):
    python ml/vision/export.py                         # ONNX, verify on 50 test images
    python ml/vision/export.py --format torchscript --verify 20
    python ml/vision/export.py --verify 0              # export only

ONNX needs `pip install onnx==1.17.0 onnxruntime==1.20.1` (see ml/requirements-tools.txt).
The exported model has a fixed input size, so --imgsz must equal
inference.yaml's model.image_size (the default is read from there).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml  # noqa: E402

from ml.vision.paths import ARTIFACTS_DIR, VISION_DIR, repo_path  # noqa: E402

_INF_CFG = yaml.safe_load((VISION_DIR / "config" / "inference.yaml").read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--weights", default=_INF_CFG["model"]["weights_path"], help="trained .pt weights")
    p.add_argument("--format", default="onnx", choices=["onnx", "torchscript"], help="export format")
    p.add_argument("--imgsz", type=int, default=_INF_CFG["model"]["image_size"], help="fixed input size")
    p.add_argument("--verify", type=int, default=50, help="test images to compare on (0 = skip)")
    p.add_argument("--images", default="ml/vision/data/test/images", help="images used for verification")
    p.add_argument("--report", default=str(ARTIFACTS_DIR / "metrics" / "export_report.json"))
    return p.parse_args()


def export(weights: Path, fmt: str, imgsz: int) -> Path:
    from ultralytics import YOLO

    out = YOLO(str(weights)).export(format=fmt, imgsz=imgsz, device="cpu", dynamic=False, simplify=False)
    return Path(out)


def numerical_parity(original: Path, exported: Path, images: list[Path], imgsz: int) -> dict:
    """Same square-letterboxed input to both models; detections must agree."""
    import numpy as np
    from PIL import Image
    from ultralytics import YOLO
    from ultralytics.data.augment import LetterBox

    models = [YOLO(str(original)), YOLO(str(exported), task="detect")]
    conf = _INF_CFG["confidence"]["detection_threshold"]
    iou = _INF_CFG["nms"]["iou_threshold"]

    def detections(model, image) -> dict[str, float]:
        r = model.predict(image, imgsz=imgsz, conf=conf, iou=iou, verbose=False)[0]
        return {model.names[int(c)]: float(s) for c, s in zip(r.boxes.cls, r.boxes.conf)}

    agree, diffs = 0, []
    for path in images:
        rgb = np.asarray(Image.open(path).convert("RGB"))
        square = LetterBox((imgsz, imgsz), auto=False)(image=np.ascontiguousarray(rgb[..., ::-1]))
        a, b = (detections(m, square) for m in models)
        agree += set(a) == set(b)
        diffs += [abs(a[k] - b[k]) for k in set(a) & set(b)]
    return {
        "images_compared": len(images),
        "identical_label_sets": agree,
        "max_confidence_diff": round(max(diffs), 6) if diffs else 0.0,
        "passed": agree == len(images) and (max(diffs) if diffs else 0.0) < 0.01,
    }


def verify(original: Path, exported: Path, images: list[Path], imgsz: int) -> dict:
    """Compare pipeline output (labels, confidences) of the original vs exported model."""
    from ultralytics import YOLO

    from ml.vision.inference import run_inference

    cfg = {**_INF_CFG, "model": {**_INF_CFG["model"], "image_size": imgsz}}
    models = {"pytorch": YOLO(str(original)), "exported": YOLO(str(exported), task="detect")}
    same_labels, conf_diffs, mismatches = 0, [], []
    timings = {name: [] for name in models}
    for path in images:
        data = path.read_bytes()
        out = {}
        for name, model in models.items():
            t = time.perf_counter()
            out[name] = {i["label"]: i["confidence"] for i in run_inference(data, model=model, infer_cfg=cfg).items}
            timings[name].append((time.perf_counter() - t) * 1000)
        a, b = out["pytorch"], out["exported"]
        if set(a) == set(b):
            same_labels += 1
        else:
            mismatches.append({"image": path.name, "pytorch": a, "exported": b})
        conf_diffs += [abs(a[k] - b[k]) for k in set(a) & set(b)]
    n = len(images)
    return {
        "images_compared": n,
        "identical_label_sets": same_labels,
        "label_set_agreement": round(same_labels / n, 4) if n else None,
        "max_confidence_diff": round(max(conf_diffs), 4) if conf_diffs else 0.0,
        "mean_confidence_diff": round(statistics.mean(conf_diffs), 4) if conf_diffs else 0.0,
        "mean_ms_pytorch": round(statistics.mean(timings["pytorch"][1:] or timings["pytorch"]), 1) if n else None,
        "mean_ms_exported": round(statistics.mean(timings["exported"][1:] or timings["exported"]), 1) if n else None,
        "mismatches": mismatches[:10],
    }


def main() -> None:
    args = parse_args()
    weights = repo_path(args.weights)
    if not weights.exists():
        sys.exit(f"ERROR: weights not found at {weights}. Train first: python ml/vision/train.py")

    print(f"Exporting {weights.name} -> {args.format} (imgsz={args.imgsz}, CPU)")
    exported = export(weights, args.format, args.imgsz)
    report = {
        "weights": str(weights),
        "format": args.format,
        "exported": str(exported),
        "exported_size_mb": round(exported.stat().st_size / 2**20, 2),
        "imgsz": args.imgsz,
    }
    if args.verify > 0:
        images = sorted(p for p in repo_path(args.images).iterdir()
                        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})[: args.verify]
        print(f"Verifying on {len(images)} images through run_inference() ...")
        report["numerical_parity"] = numerical_parity(weights, exported, images, args.imgsz)
        report["pipeline_parity"] = verify(weights, exported, images, args.imgsz)

    out = repo_path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nReport saved to: {out}")


if __name__ == "__main__":
    main()
