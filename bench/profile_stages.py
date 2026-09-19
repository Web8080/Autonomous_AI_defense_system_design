#!/usr/bin/env python3
"""Stage profiler for the inference serving layer (Phase 4).

Author: Victor.I

Measures preprocess / infer / postprocess on CPU, MPS, or CUDA without Jetson.
Same JSON contract is intended for Orin nightly jobs later.

Usage:
  python bench/profile_stages.py --weights data/models/visdrone-yolov8n.pt \\
      --images data/visdrone-yolo/val/images --limit 40 --device mps
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "services" / "inference_service"))


def percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    k = (len(ys) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ys) - 1)
    if f == c:
        return ys[f]
    return ys[f] + (ys[c] - ys[f]) * (k - f)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", required=True)
    parser.add_argument("--images", required=True, help="dir of jpg/png")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--device", default="cpu", help="label recorded in output (mps/cuda/cpu/orin-*)")
    parser.add_argument("--runtime", default="auto")
    parser.add_argument("--out", default="bench/results/stages.json")
    args = parser.parse_args()

    from runtimes import resolve_runtime
    import numpy as np
    from PIL import Image

    rt = resolve_runtime(args.weights, args.runtime)
    if not rt.loaded():
        print("model not loaded", file=sys.stderr)
        sys.exit(1)

    imgs = sorted(
        list(Path(args.images).glob("*.jpg"))
        + list(Path(args.images).glob("*.png"))
    )[: args.limit]
    if not imgs:
        print("no images", file=sys.stderr)
        sys.exit(1)

    pre_ms: list[float] = []
    inf_ms: list[float] = []
    post_ms: list[float] = []
    end_ms: list[float] = []
    det_counts: list[int] = []

    for path in imgs:
        t0 = time.perf_counter()
        t_pre = time.perf_counter()
        arr = np.array(Image.open(path).convert("RGB"))
        pre_ms.append((time.perf_counter() - t_pre) * 1000)

        t_inf = time.perf_counter()
        dets = rt.predict(arr)
        inf_ms.append((time.perf_counter() - t_inf) * 1000)

        t_post = time.perf_counter()
        # Normalize bbox like production
        h, w = arr.shape[0], arr.shape[1]
        _ = [
            [
                d.xyxy[0] / w,
                d.xyxy[1] / h,
                d.xyxy[2] / w,
                d.xyxy[3] / h,
            ]
            for d in dets
        ]
        post_ms.append((time.perf_counter() - t_post) * 1000)
        end_ms.append((time.perf_counter() - t0) * 1000)
        det_counts.append(len(dets))

    report = {
        "device": args.device,
        "runtime": rt.name,
        "weights": args.weights,
        "frames": len(imgs),
        "mean_detections": statistics.mean(det_counts) if det_counts else 0,
        "preprocess_ms": {
            "p50": percentile(pre_ms, 50),
            "p95": percentile(pre_ms, 95),
            "p99": percentile(pre_ms, 99),
        },
        "infer_ms": {
            "p50": percentile(inf_ms, 50),
            "p95": percentile(inf_ms, 95),
            "p99": percentile(inf_ms, 99),
        },
        "postprocess_ms": {
            "p50": percentile(post_ms, 50),
            "p95": percentile(post_ms, 95),
            "p99": percentile(post_ms, 99),
        },
        "end_to_end_ms": {
            "p50": percentile(end_ms, 50),
            "p95": percentile(end_ms, 95),
            "p99": percentile(end_ms, 99),
        },
        "sustained_fps_est": round(1000.0 / (percentile(end_ms, 50) or 1e9), 2),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
