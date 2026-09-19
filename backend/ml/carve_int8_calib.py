#!/usr/bin/env python3
"""Carve INT8 calibration images from VisDrone train (never the frozen val).

Author: Victor.I

Held-out train slice for TensorRT calibration on Orin. Writes a text list of
absolute paths consumed later by trtexec / Polygraphy on the device.
"""
from __future__ import annotations

import argparse
import hashlib
import random
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-images", default="data/visdrone-yolo/train/images")
    parser.add_argument("--out", default="data/calib/visdrone-int8.txt")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    root = Path(args.train_images)
    imgs = sorted(root.glob("*.jpg")) + sorted(root.glob("*.png"))
    if not imgs:
        raise SystemExit(f"no images under {root}")
    rng = random.Random(args.seed)
    sample = imgs if len(imgs) <= args.count else rng.sample(imgs, args.count)
    sample = sorted(sample, key=lambda p: p.name)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [str(p.resolve()) for p in sample]
    out.write_text("\n".join(lines) + "\n")
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    meta = out.with_suffix(".sha256")
    meta.write_text(digest + "\n")
    print(f"wrote {len(lines)} paths -> {out}")
    print(f"list_sha256={digest}")


if __name__ == "__main__":
    main()
