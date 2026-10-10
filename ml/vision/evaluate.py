"""
Post-training evaluation script for the EcoLink YOLOv8n pipeline.

Runs the trained model on the test (or val) split and reports per-class
precision, recall, mAP50, and mAP50-95.

Usage:
    python ml/vision/evaluate.py [options]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python ml/vision/evaluate.py` from any directory
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.vision.paths import ARTIFACTS_DIR, repo_path, resolve_dataset_yaml  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the EcoLink YOLOv8n model on the test split.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--weights", type=str, default="ml/vision/artifacts/weights/best.pt",
                        help="Path to trained weights (.pt)")
    parser.add_argument("--data",    type=str, default="ml/vision/config/dataset.yaml",
                        help="Dataset YAML path")
    parser.add_argument("--imgsz",   type=int, default=640,
                        help="Input image size")
    parser.add_argument("--conf",    type=float, default=0.50,
                        help="Confidence threshold")
    parser.add_argument("--iou",     type=float, default=0.45,
                        help="IoU threshold for NMS")
    parser.add_argument("--split",   type=str, default="test",
                        choices=["test", "val"],
                        help="Dataset split to evaluate on")
    parser.add_argument("--device",  type=str, default="auto",
                        help="'auto', 'cpu', or 'cuda'")
    parser.add_argument("--out",     type=str,
                        default="ml/vision/artifacts/metrics/eval_report.json",
                        help="Path to write JSON evaluation report")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    weights_path = repo_path(args.weights)
    if not weights_path.exists():
        print(f"ERROR: weights not found at '{weights_path}'")
        print("Train the model first:  python ml/vision/train.py")
        sys.exit(1)

    # Resolve device
    if args.device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    else:
        device = args.device

    print("=" * 60)
    print("EcoLink YOLOv8n Evaluation")
    print("=" * 60)
    print(f"  Weights : {weights_path}")
    print(f"  Data    : {args.data}")
    print(f"  Split   : {args.split}")
    print(f"  Conf    : {args.conf}")
    print(f"  IoU     : {args.iou}")
    print(f"  Device  : {device}")
    print("=" * 60)

    from ultralytics import YOLO
    import yaml

    model = YOLO(str(weights_path))

    data_yaml = resolve_dataset_yaml(args.data)
    metrics = model.val(
        data=str(data_yaml),
        split=args.split,
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.iou,
        device=device,
        project=str(ARTIFACTS_DIR / "eval_runs"),
        name=args.split,
        exist_ok=True,
        verbose=True,
    )

    # Load class names for the report
    with open(data_yaml, encoding="utf-8") as fh:
        dataset_cfg = yaml.safe_load(fh)
    class_names: dict = dataset_cfg.get("names", {})

    # Build report
    report: dict = {
        "mAP50": float(metrics.box.map50),
        "mAP50_95": float(metrics.box.map),
        "per_class": {},
    }

    # Per-class metrics (ultralytics stores them in metrics.box.ap_class_index and
    # metrics.box.ap per-class arrays)
    try:
        ap_per_class = metrics.box.ap_class_index  # class indices
        ap_values_50 = metrics.box.ap50             # mAP50 per class
        ap_values = metrics.box.ap                  # mAP50-95 per class
        precision = metrics.box.p                   # precision per class
        recall = metrics.box.r                      # recall per class

        for idx, cls_idx in enumerate(ap_per_class):
            label = class_names.get(int(cls_idx), str(cls_idx))
            report["per_class"][label] = {
                "precision": float(precision[idx]) if precision is not None else None,
                "recall":    float(recall[idx])    if recall is not None else None,
                "mAP50":     float(ap_values_50[idx]) if ap_values_50 is not None else None,
                "mAP50_95":  float(ap_values[idx]) if ap_values is not None else None,
            }
    except (AttributeError, TypeError, IndexError) as exc:
        print(f"WARNING: Could not extract per-class metrics: {exc}")

    # Print table
    print("\n" + "=" * 70)
    print(f"{'Class':<15} {'Precision':>10} {'Recall':>10} {'mAP50':>10} {'mAP50-95':>10}")
    print("-" * 70)
    for label, m in report["per_class"].items():
        p = f"{m['precision']:.4f}" if m["precision"] is not None else "n/a"
        r = f"{m['recall']:.4f}"    if m["recall"] is not None else "n/a"
        m50 = f"{m['mAP50']:.4f}"   if m["mAP50"] is not None else "n/a"
        m95 = f"{m['mAP50_95']:.4f}" if m["mAP50_95"] is not None else "n/a"
        print(f"{label:<15} {p:>10} {r:>10} {m50:>10} {m95:>10}")
    print("-" * 70)
    print(f"{'Overall':<15} {'':>10} {'':>10} {report['mAP50']:>10.4f} {report['mAP50_95']:>10.4f}")
    print("=" * 70)

    # Save report
    out_path = repo_path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport saved to: {out_path}")

    # Save confusion matrix plot
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if hasattr(metrics, "confusion_matrix"):
            # ultralytics stores [predicted, true] with an extra background row/column;
            # transpose so rows are the true class, and label the background slot.
            cm = metrics.confusion_matrix.matrix.T
            fig, ax = plt.subplots(figsize=(8, 7))
            import seaborn as sns
            labels = [class_names.get(i, str(i)) for i in range(len(class_names))] + ["background"]
            sns.heatmap(cm, annot=True, fmt=".0f", xticklabels=labels,
                        yticklabels=labels, ax=ax, cmap="Blues")
            ax.set_xlabel("Predicted")
            ax.set_ylabel("True")
            ax.set_title("Confusion Matrix")
            plt.tight_layout()
            cm_path = ARTIFACTS_DIR / "confusion_matrix.png"
            fig.savefig(cm_path, dpi=120)
            plt.close(fig)
            print(f"Confusion matrix saved to: {cm_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: Could not save confusion matrix: {exc}")

    # Per-class JSON
    per_class_path = ARTIFACTS_DIR / "metrics" / "per_class.json"
    per_class_path.parent.mkdir(parents=True, exist_ok=True)
    per_class_path.write_text(json.dumps(report["per_class"], indent=2), encoding="utf-8")

    # Overall JSON
    overall_path = ARTIFACTS_DIR / "metrics" / "overall.json"
    overall_path.write_text(
        json.dumps({"mAP50": report["mAP50"], "mAP50_95": report["mAP50_95"]}, indent=2),
        encoding="utf-8",
    )
    print(f"Per-class metrics: {per_class_path}")
    print(f"Overall metrics:   {overall_path}")


if __name__ == "__main__":
    main()
