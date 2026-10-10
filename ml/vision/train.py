"""
Train YOLOv8n on the EcoLink waste dataset.

Usage:
    python ml/vision/train.py [options]

Defaults are safe for CPU machines:
    epochs=100, batch=16, imgsz=640, lr0=0.01, device=auto, seed=42

After training, the best weights are copied to
ml/vision/artifacts/weights/best.pt so inference.yaml's default path
resolves automatically.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Allow `python ml/vision/train.py` from any directory
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.vision.paths import repo_path, resolve_dataset_yaml  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fine-tune YOLOv8n on the EcoLink 7-class waste dataset.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--epochs",      type=int,   default=100,                            help="Number of training epochs")
    parser.add_argument("--batch-size",  type=int,   default=16,                             help="Batch size (use 4–8 on CPU)")
    parser.add_argument("--imgsz",       type=int,   default=640,                            help="Input image size")
    parser.add_argument("--lr0",         type=float, default=0.01,                           help="Initial learning rate")
    parser.add_argument("--device",      type=str,   default="auto",                         help="'auto', 'cpu', or 'cuda'")
    parser.add_argument("--seed",        type=int,   default=42,                             help="Random seed for reproducibility")
    parser.add_argument("--patience",    type=int,   default=10,                             help="Early stopping patience (epochs)")
    parser.add_argument("--resume",      action="store_true",                                help="Resume training from last checkpoint")
    parser.add_argument("--weights",     type=str,   default="yolov8n.pt",                   help="Starting weights (ultralytics auto-downloads)")
    parser.add_argument("--data",        type=str,   default="ml/vision/config/dataset.yaml", help="Dataset YAML path")
    parser.add_argument("--project",     type=str,   default="ml/vision/artifacts",           help="Experiment output directory")
    parser.add_argument("--name",        type=str,   default="train",                         help="Experiment name")
    parser.add_argument("--conf",        type=float, default=0.50,                            help="Confidence threshold for validation")
    parser.add_argument("--iou",         type=float, default=0.45,                            help="IoU threshold for NMS")
    parser.add_argument("--workers",     type=int,   default=4,                               help="Dataloader workers (lower on low-RAM machines)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

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
    print("EcoLink YOLOv8n Training")
    print("=" * 60)
    print(f"  Device   : {device}")
    print(f"  Weights  : {args.weights}")
    print(f"  Data     : {args.data}")
    print(f"  Epochs   : {args.epochs}")
    print(f"  Batch    : {args.batch_size}")
    print(f"  Img size : {args.imgsz}")
    print(f"  LR0      : {args.lr0}")
    print(f"  Patience : {args.patience}")
    print(f"  Seed     : {args.seed}")
    print(f"  Resume   : {args.resume}")
    print(f"  Project  : {args.project}")
    print(f"  Name     : {args.name}")
    print("=" * 60)

    from ultralytics import YOLO

    model = YOLO(args.weights)

    results = model.train(
        data=str(resolve_dataset_yaml(args.data)),
        epochs=args.epochs,
        batch=args.batch_size,
        imgsz=args.imgsz,
        lr0=args.lr0,
        device=device,
        patience=args.patience,
        seed=args.seed,
        resume=args.resume,
        project=str(repo_path(args.project)),
        name=args.name,
        conf=args.conf,
        iou=args.iou,
        workers=args.workers,
        verbose=True,
    )

    # Copy best weights to canonical location.  Use the trainer's real save_dir:
    # ultralytics auto-increments the run name (train, train2, ...) when it
    # already exists, so project/name may point at an older run.
    run_dir = Path(model.trainer.save_dir)
    src_best = run_dir / "weights" / "best.pt"
    dst_best = repo_path("ml/vision/artifacts/weights/best.pt")
    dst_best.parent.mkdir(parents=True, exist_ok=True)

    if src_best.exists():
        shutil.copy2(src_best, dst_best)
        print(f"\nBest weights saved to: {dst_best}")
    else:
        print(f"\nWARNING: best.pt not found at {src_best}")

    # Print final metrics
    try:
        map50 = results.box.map50
        map50_95 = results.box.map
        print(f"\nFinal mAP50:    {map50:.4f}")
        print(f"Final mAP50-95: {map50_95:.4f}")
    except AttributeError:
        print("\nTraining complete. Check metrics in tensorboard or ultralytics results.csv")


if __name__ == "__main__":
    main()
