"""
Fine-tune existing YOLO on new threat classes or sensor modality.
Loads base weights from MODEL_PATH or S3. Same dataset layout as train_yolo.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ml.config import MODEL_PATH, RUNS_DIR, S3_BUCKET_MODELS


def ensure_base_weights(base: str) -> str:
    path = Path(base)
    if path.exists():
        return base
    if S3_BUCKET_MODELS:
        try:
            import boto3
            Path("weights").mkdir(exist_ok=True)
            local = Path("weights") / "base.pt"
            boto3.client("s3").download_file(S3_BUCKET_MODELS, "models/base.pt", str(local))
            return str(local)
        except Exception as e:
            print("S3 model download failed:", e)
    return base


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=str, default=MODEL_PATH or "yolov8n.pt")
    parser.add_argument("--data-yaml", type=str, required=True)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--freeze", type=int, default=10)
    parser.add_argument("--project", type=str, default=str(RUNS_DIR / "finetune"))
    args = parser.parse_args()

    base = ensure_base_weights(args.base)
    try:
        from ultralytics import YOLO
    except ImportError:
        print("Install ultralytics: pip install ultralytics")
        sys.exit(1)

    model = YOLO(base)
    model.train(
        data=args.data_yaml,
        epochs=args.epochs,
        freeze=args.freeze,
        project=args.project,
        exist_ok=True,
    )
    best = Path(args.project) / "train" / "weights" / "best.pt"
    print("Best weights:", best)


if __name__ == "__main__":
    main()
