"""
Train YOLO model for threat detection. Supports local dataset dir or S3/Roboflow.
Set DATASET_PATH or S3_BUCKET_DATASETS / ROBOFLOW_* in .env. Request credentials before cloud use.
"""
import os
import argparse
from pathlib import Path

# Optional: from ultralytics import YOLO

DATASET_PATH = os.getenv("DATASET_PATH", "data/raw")
S3_BUCKET = os.getenv("S3_BUCKET_DATASETS", "")
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY", "")
ROBOFLOW_WORKSPACE = os.getenv("ROBOFLOW_WORKSPACE", "")


def download_dataset_local(data_dir: Path) -> Path:
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        print("Created dataset dir; add images and labels (YOLO format).")
    return data_dir


def download_from_s3(data_dir: Path) -> Path:
    if not S3_BUCKET:
        return data_dir
    try:
        import boto3
        s3 = boto3.client("s3")
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix="datasets/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                local = data_dir / key.replace("datasets/", "")
                local.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(S3_BUCKET, key, str(local))
    except Exception as e:
        print("S3 download failed (check AWS credentials):", e)
    return data_dir


def download_from_roboflow(data_dir: Path) -> Path:
    if not ROBOFLOW_API_KEY or not ROBOFLOW_WORKSPACE:
        return data_dir
    try:
        from roboflow import Roboflow
        rf = Roboflow(api_key=ROBOFLOW_API_KEY)
        project = rf.workspace(ROBOFLOW_WORKSPACE).project("defense-threats")
        version = project.version(1)
        version.download("yolov8", location=str(data_dir))
    except Exception as e:
        print("Roboflow download failed (check API key and project):", e)
    return data_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--model", type=str, default="yolov8n.pt")
    parser.add_argument("--data-yaml", type=str, default="")
    args = parser.parse_args()
    data_dir = Path(DATASET_PATH)
    download_dataset_local(data_dir)
    if S3_BUCKET:
        download_from_s3(data_dir)
    if ROBOFLOW_API_KEY:
        download_from_roboflow(data_dir)
    data_yaml = args.data_yaml or str(data_dir / "data.yaml")
    if not Path(data_yaml).exists():
        print("No data.yaml found. Create one with train/val paths and class names.")
        return
    # model = YOLO(args.model)
    # model.train(data=data_yaml, epochs=args.epochs, batch=args.batch, project="runs/train")
    print("Training placeholder. Uncomment YOLO train and set DATASET_PATH or S3/Roboflow.")


if __name__ == "__main__":
    main()
