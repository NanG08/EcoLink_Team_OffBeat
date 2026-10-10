#!/usr/bin/env python
"""Compare the Lambda fallback (ONNX Runtime + numpy) with the reference pipeline (ml/vision).

    python scripts/check_vision_fallback.py path/to/photos [--limit 50]

For every image it runs
  * backend/ecolink/core/vision_fallback.py on ml/vision/artifacts/weights/best.onnx (what Lambda runs), and
  * ml/vision/inference.py on best.pt through ultralytics (the training-time reference),
then reports how often they return the same labels and how far the confidences differ.

Needs the ML tooling (torch, ultralytics): see ml/vision/requirements.txt. Run from the repo root.
Use photos that are NOT in the training set (for example ml/vision/data/test/images).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--onnx", type=Path, default=ROOT / "ml" / "vision" / "artifacts" / "weights" / "best.onnx")
    args = parser.parse_args()

    images = sorted(p for p in args.folder.rglob("*") if p.suffix.lower() in EXTENSIONS)[: args.limit]
    if not images:
        print(f"No images found in {args.folder}")
        return 1

    try:
        from ml.vision.inference import run_inference
    except ImportError as exc:
        print(f"The reference pipeline needs the ML tooling ({exc}).\n"
              "Install it with: pip install -r ml/vision/requirements.txt")
        return 1
    from ecolink.core.vision_fallback import OnnxDetector

    os.chdir(ROOT)  # inference.yaml uses a repo-relative weights path
    detector = OnnxDetector(args.onnx)

    same_labels = 0
    diffs: list[float] = []
    for path in images:
        data = path.read_bytes()
        mine = {i["label"]: i["confidence"] for i in detector.detect(data)}
        ref = {i["label"]: i["confidence"] for i in run_inference(data).to_api_dict()["items"]}
        agree = set(mine) == set(ref)
        same_labels += agree
        diffs += [abs(mine[label] - ref[label]) for label in set(mine) & set(ref)]
        print(f"{'same ' if agree else 'DIFF '} {path.name:40} fallback={mine}  reference={ref}")

    print(f"\nimages compared      : {len(images)}")
    print(f"identical label sets : {same_labels}/{len(images)} ({100 * same_labels / len(images):.0f}%)")
    if diffs:
        print(f"confidence difference: mean {sum(diffs) / len(diffs):.3f}, max {max(diffs):.3f} (shared labels)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
