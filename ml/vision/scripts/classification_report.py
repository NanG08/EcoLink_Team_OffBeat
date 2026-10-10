"""
Per-class ACCURACY table + confusion matrix for the deployed pipeline.

evaluate.py reports box-level detection metrics (precision/recall/mAP).
This script answers the product question instead: for each photo, did the API
(run_inference: threshold 0.50, one item per label) return the right classes?
Every image of the split is classified; each class is scored as a yes/no
"present in this photo" decision.

Usage (from repo root):
    python ml/vision/scripts/classification_report.py              # test split
    python ml/vision/scripts/classification_report.py --split val --weights path/to/best.pt

Writes to ml/vision/artifacts/metrics/:
    classification_report.json   all numbers
    classification_report.md     the per-class table (weak classes marked)
    classification_confusion.png top-1 confusion matrix on single-class photos
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ml.vision.paths import ARTIFACTS_DIR, VISION_DIR, repo_path  # noqa: E402

WEAK_F1 = 0.70          # classes below this F1 are flagged as weak
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def load_split(split_dir: Path, class_names: list[str]) -> list[tuple[Path, set[str]]]:
    """[(image_path, ground-truth label set)] from a YOLO split directory."""
    out = []
    for img in sorted((split_dir / "images").iterdir()):
        if img.suffix.lower() not in _IMG_EXTS:
            continue
        label_file = split_dir / "labels" / f"{img.stem}.txt"
        lines = label_file.read_text(encoding="utf-8").splitlines() if label_file.exists() else []
        out.append((img, {class_names[int(line.split()[0])] for line in lines if line.strip()}))
    return out


def _ratio(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


def score(records: list[tuple[set[str], list[dict]]], class_names: list[str],
          hazardous: set[str]) -> dict[str, Any]:
    """records: [(ground-truth labels, API items)] -> full report dict."""
    n = len(records)
    per_class: dict[str, Any] = {}
    for cls in class_names:
        tp = fp = fn = tn = 0
        for gt, items in records:
            pred = cls in {i["label"] for i in items}
            truth = cls in gt
            tp += pred and truth
            fp += pred and not truth
            fn += truth and not pred
            tn += not pred and not truth
        precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        f1 = _ratio(2 * precision * recall, precision + recall)
        per_class[cls] = {
            "accuracy": _ratio(tp + tn, n), "precision": precision, "recall": recall, "f1": f1,
            "support": tp + fn, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "weak": f1 < WEAK_F1,
        }

    confusion = {cls: Counter() for cls in class_names}
    for gt, items in records:
        if len(gt) == 1:
            confusion[next(iter(gt))][items[0]["label"] if items else "none"] += 1

    gt_h = [bool(gt & hazardous) for gt, _ in records]
    pred_h = [any(i["hazardous"] for i in items) for _, items in records]
    h_tp = sum(g and p for g, p in zip(gt_h, pred_h))
    h_fp = sum(p and not g for g, p in zip(gt_h, pred_h))
    h_fn = sum(g and not p for g, p in zip(gt_h, pred_h))

    macro = {k: round(sum(v[k] for v in per_class.values()) / len(class_names), 4)
             for k in ("accuracy", "precision", "recall", "f1")}
    return {
        "images": n,
        "per_class": per_class,
        "macro_avg": macro,
        "exact_match_accuracy": _ratio(sum({i["label"] for i in items} == gt for gt, items in records), n),
        "images_with_no_prediction": sum(not items for _, items in records),
        "weak_classes": [c for c, v in per_class.items() if v["weak"]],
        "hazard_detection": {"precision": _ratio(h_tp, h_tp + h_fp), "recall": _ratio(h_tp, h_tp + h_fn),
                             "missed_images": h_fn, "hazardous_images": h_tp + h_fn},
        "top1_confusion_single_class": {c: dict(v) for c, v in confusion.items()},
    }


def to_markdown(report: dict[str, Any], title: str) -> str:
    rows = [f"# {title}", "",
            f"Images: {report['images']} · exact-match accuracy {report['exact_match_accuracy']:.2f} · "
            f"no prediction on {report['images_with_no_prediction']} images", "",
            "| Class | Accuracy | Precision | Recall | F1 | Support | |",
            "|---|---:|---:|---:|---:|---:|---|"]
    for cls, m in report["per_class"].items():
        flag = "**weak**" if m["weak"] else ""
        rows.append(f"| {cls} | {m['accuracy']:.3f} | {m['precision']:.3f} | {m['recall']:.3f} | "
                    f"{m['f1']:.3f} | {m['support']} | {flag} |")
    mac = report["macro_avg"]
    rows.append(f"| **macro avg** | {mac['accuracy']:.3f} | {mac['precision']:.3f} | {mac['recall']:.3f} | "
                f"{mac['f1']:.3f} | | |")
    h = report["hazard_detection"]
    rows += ["", f"Hazard detection (photo contains e-waste or a battery → at least one `hazardous: true` item): "
                 f"precision {h['precision']:.3f}, recall {h['recall']:.3f} "
                 f"({h['missed_images']} of {h['hazardous_images']} hazardous photos missed).",
             "", f"Weak classes (F1 < {WEAK_F1}): {', '.join(report['weak_classes']) or 'none'}.",
             "", "Accuracy counts true negatives, so it looks high for rare classes; read it with recall and F1."]
    return "\n".join(rows) + "\n"


def save_confusion_png(report: dict[str, Any], class_names: list[str], path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    cols = class_names + ["none"]
    conf = report["top1_confusion_single_class"]
    mat = np.array([[conf[g].get(p, 0) for p in cols] for g in class_names])
    norm = mat / np.maximum(mat.sum(1, keepdims=True), 1)
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(cols)), cols, rotation=45, ha="right")
    ax.set_yticks(range(len(class_names)), class_names)
    for i in range(len(class_names)):
        for j in range(len(cols)):
            if mat[i, j]:
                ax.text(j, i, int(mat[i, j]), ha="center", va="center",
                        color="white" if norm[i, j] > 0.5 else "black", fontsize=9)
    ax.set_xlabel("top-1 API prediction")
    ax.set_ylabel("true class")
    ax.set_title(title)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> None:
    import yaml

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--split", default="test", choices=["train", "val", "test"])
    p.add_argument("--data-dir", default="ml/vision/data")
    p.add_argument("--weights", default=None, help="defaults to inference.yaml model.weights_path")
    p.add_argument("--out-dir", default=str(ARTIFACTS_DIR / "metrics"))
    args = p.parse_args()

    from ultralytics import YOLO

    from ml.vision.inference import run_inference
    from ml.vision.schemas import load_classes_config

    cfg = load_classes_config()
    inf = yaml.safe_load((VISION_DIR / "config" / "inference.yaml").read_text(encoding="utf-8"))
    weights = repo_path(args.weights or inf["model"]["weights_path"])
    if not weights.exists():
        sys.exit(f"ERROR: weights not found at {weights}")
    model = YOLO(str(weights))

    split = load_split(repo_path(args.data_dir) / args.split, cfg.class_names)
    print(f"Classifying {len(split)} {args.split} images with {weights.name} ...")
    records = [(gt, run_inference(img.read_bytes(), model=model).items) for img, gt in split]
    report = score(records, cfg.class_names, set(cfg.hazardous_labels))
    report.update({"split": args.split, "weights": str(weights), "image_size": inf["model"]["image_size"],
                   "threshold": inf["confidence"]["detection_threshold"]})

    out = repo_path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    title = f"Per-class accuracy — {args.split} split ({len(split)} images)"
    (out / "classification_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "classification_report.md").write_text(to_markdown(report, title), encoding="utf-8")
    save_confusion_png(report, cfg.class_names, out / "classification_confusion.png", title)
    print(to_markdown(report, title))
    print(f"Saved: {out / 'classification_report.json'}, .md and classification_confusion.png")


if __name__ == "__main__":
    main()
