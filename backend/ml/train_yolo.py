"""
Train YOLO (Ultralytics) for threat detection. Local dir, S3, or Roboflow.
Output: runs/train/exp/weights/best.pt. Logs to MLflow if MLFLOW_TRACKING_URI set.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add backend root for shared and config
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ml.config import DATASET_PATH, RUNS_DIR, S3_BUCKET_DATASETS, ROBOFLOW_API_KEY, ROBOFLOW_WORKSPACE


def download_dataset_local(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def download_from_s3(data_dir: Path, bucket: str) -> Path:
    if not bucket:
        return data_dir
    try:
        import boto3
        s3 = boto3.client("s3")
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix="datasets/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                local = data_dir / key.replace("datasets/", "").lstrip("/")
                local.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket, key, str(local))
    except Exception as e:
        print("S3 download failed:", e)
    return data_dir


def download_from_roboflow(data_dir: Path, api_key: str, workspace: str) -> Path:
    if not api_key or not workspace:
        return data_dir
    try:
        from roboflow import Roboflow
        rf = Roboflow(api_key=api_key)
        project = rf.workspace(workspace).project("defense-threats")
        version = project.version(1)
        version.download("yolov8", location=str(data_dir))
    except Exception as e:
        print("Roboflow download failed:", e)
    return data_dir


def run_training(data_yaml: str, epochs: int, batch: int, model_name: str, project: str) -> Path:
    try:
        from ultralytics import YOLO
    except ImportError:
        print("Install ultralytics: pip install ultralytics")
        sys.exit(1)
    model = YOLO(model_name)
    results = model.train(
        data=data_yaml,
        epochs=epochs,
        batch=batch,
        project=project,
        exist_ok=True,
    )
    best_weights = Path(project) / "train" / "weights" / "best.pt"
    if not best_weights.exists() and results.save_dir:
        best_weights = Path(results.save_dir) / "weights" / "best.pt"
    return best_weights if best_weights.exists() else Path(project) / "train" / "weights" / "best.pt"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--model", type=str, default="yolov8n.pt")
    parser.add_argument("--data-yaml", type=str, default="")
    parser.add_argument("--project", type=str, default=str(RUNS_DIR / "train"))
    args = parser.parse_args()

    data_dir = DATASET_PATH
    download_dataset_local(data_dir)
    download_from_s3(data_dir, S3_BUCKET_DATASETS)
    download_from_roboflow(data_dir, ROBOFLOW_API_KEY, ROBOFLOW_WORKSPACE)

    data_yaml = args.data_yaml or str(data_dir / "data.yaml")
    if not Path(data_yaml).exists():
        print("No data.yaml. Create one with train/val paths and class names.")
        sys.exit(1)

    if os.getenv("MLFLOW_TRACKING_URI"):
        import mlflow
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI"))
        with mlflow.start_run(run_name="yolo_train"):
            mlflow.log_params({"epochs": args.epochs, "batch": args.batch, "model": args.model})
            best = run_training(data_yaml, args.epochs, args.batch, args.model, args.project)
            if best.exists():
                mlflow.log_artifact(str(best), artifact_path="model")
    else:
        best = run_training(data_yaml, args.epochs, args.batch, args.model, args.project)

    print("Best weights:", best)


if __name__ == "__main__":
    main()
