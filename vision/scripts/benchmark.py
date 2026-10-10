"""
CPU inference latency benchmark for the EcoLink YOLOv8n pipeline.

Measures cold-start and warm inference latency using synthetic noise images.
Requires trained model weights at the default path.

Usage:
    python ml/vision/scripts/benchmark.py [options]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark EcoLink YOLOv8n CPU inference latency.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--weights",    type=str, default="ml/vision/artifacts/weights/best.pt",
                        help="Path to model weights")
    parser.add_argument("--imgsz",      type=int, default=640,
                        help="Input image size")
    parser.add_argument("--n-runs",     type=int, default=50,
                        help="Number of warm inference runs")
    parser.add_argument("--warmup",     type=int, default=3,
                        help="Number of warmup runs (discarded)")
    return parser.parse_args()


def make_synthetic_image(imgsz: int):
    """Create a synthetic RGB PIL Image of all zeros (fastest, no real data needed)."""
    from PIL import Image
    import numpy as np
    arr = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def main() -> None:
    args = parse_args()

    weights_path = Path(args.weights)
    if not weights_path.exists():
        print(f"ERROR: weights not found at '{weights_path}'")
        print("Train the model first:  python ml/vision/train.py")
        sys.exit(1)

    print("=" * 60)
    print("EcoLink YOLOv8n Benchmark")
    print("=" * 60)
    print(f"  Weights : {weights_path}")
    print(f"  Img size: {args.imgsz}")
    print(f"  Warm-up : {args.warmup} runs")
    print(f"  Runs    : {args.n_runs}")
    print("=" * 60)

    # Model file size
    model_size_mb = weights_path.stat().st_size / (1024 * 1024)
    print(f"\nModel file size: {model_size_mb:.2f} MB")

    from ultralytics import YOLO

    # Cold-start timing (includes model load)
    t0 = time.perf_counter()
    model = YOLO(str(weights_path))
    model.to("cpu")
    t_load = time.perf_counter() - t0
    print(f"Model load time: {t_load * 1000:.1f} ms")

    img = make_synthetic_image(args.imgsz)

    # Cold-start inference (first predict call)
    t0 = time.perf_counter()
    _ = model(img, imgsz=args.imgsz, conf=0.25, verbose=False)
    cold_start_ms = (time.perf_counter() - t0) * 1000
    print(f"Cold-start inference: {cold_start_ms:.1f} ms")

    # Warmup runs
    for _ in range(args.warmup - 1):
        _ = model(img, imgsz=args.imgsz, conf=0.25, verbose=False)

    # Peak memory measurement during one inference
    tracemalloc.start()
    _ = model(img, imgsz=args.imgsz, conf=0.25, verbose=False)
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mem_mb = peak_mem / (1024 * 1024)

    # Warm runs timing
    latencies_ms: list[float] = []
    for i in range(args.n_runs):
        t0 = time.perf_counter()
        _ = model(img, imgsz=args.imgsz, conf=0.25, verbose=False)
        latencies_ms.append((time.perf_counter() - t0) * 1000)

    mean_ms = statistics.mean(latencies_ms)
    p50_ms = statistics.median(latencies_ms)
    p95_ms = statistics.quantiles(latencies_ms, n=20)[18]   # 95th percentile
    p99_ms = statistics.quantiles(latencies_ms, n=100)[98]  # 99th percentile
    min_ms = min(latencies_ms)
    max_ms = max(latencies_ms)
    throughput = 1000.0 / mean_ms  # images per second

    print("\n" + "=" * 60)
    print("Latency Results (warm runs)")
    print("=" * 60)
    print(f"  Mean     : {mean_ms:.1f} ms")
    print(f"  Median   : {p50_ms:.1f} ms")
    print(f"  P95      : {p95_ms:.1f} ms")
    print(f"  P99      : {p99_ms:.1f} ms")
    print(f"  Min      : {min_ms:.1f} ms")
    print(f"  Max      : {max_ms:.1f} ms")
    print(f"  Throughput: {throughput:.2f} images/sec")
    print(f"  Peak memory (inference): {peak_mem_mb:.2f} MB")
    print("=" * 60)

    results = {
        "model_size_mb": round(model_size_mb, 2),
        "model_load_ms": round(t_load * 1000, 1),
        "cold_start_ms": round(cold_start_ms, 1),
        "warm_runs": args.n_runs,
        "mean_ms": round(mean_ms, 2),
        "p50_ms": round(p50_ms, 2),
        "p95_ms": round(p95_ms, 2),
        "p99_ms": round(p99_ms, 2),
        "min_ms": round(min_ms, 2),
        "max_ms": round(max_ms, 2),
        "throughput_images_per_sec": round(throughput, 2),
        "peak_memory_mb": round(peak_mem_mb, 2),
        "image_size": args.imgsz,
        "device": "cpu",
    }

    out_path = Path("ml/vision/artifacts/benchmark.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults saved to: {out_path}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
