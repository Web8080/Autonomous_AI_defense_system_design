"""
Fine-tune existing model on new threat classes or sensor modality.
Uses same dataset layout as train_yolo.py; loads base weights from MODEL_PATH or S3.
"""
import os
import argparse
from pathlib import Path

BASE_WEIGHTS = os.getenv("MODEL_PATH", "weights/yolov8n.pt")
S3_BUCKET_MODELS = os.getenv("S3_BUCKET_MODELS", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=str, default=BASE_WEIGHTS)
    parser.add_argument("--data-yaml", type=str, required=True)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--freeze", type=int, default=10)
    args = parser.parse_args()
    if S3_BUCKET_MODELS and not Path(args.base).exists():
        try:
            import boto3
            Path("weights").mkdir(exist_ok=True)
            boto3.client("s3").download_file(
                S3_BUCKET_MODELS, "models/base.pt", args.base
            )
        except Exception as e:
            print("S3 download failed:", e)
            return
    # model = YOLO(args.base)
    # model.train(data=args.data_yaml, epochs=args.epochs, freeze=args.freeze, project="runs/finetune")
    print("Finetune placeholder. Uncomment YOLO and set MODEL_PATH or S3_BUCKET_MODELS.")


if __name__ == "__main__":
    main()
