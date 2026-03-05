"""ML pipeline config: paths, thresholds, experiment tracking."""
import os
from pathlib import Path

# Datasets
DATASET_PATH = Path(os.getenv("DATASET_PATH", "data/raw"))
S3_BUCKET_DATASETS = os.getenv("S3_BUCKET_DATASETS", "")
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY", "")
ROBOFLOW_WORKSPACE = os.getenv("ROBOFLOW_WORKSPACE", "")

# Models and export
MODEL_PATH = os.getenv("MODEL_PATH", "")
MODEL_REGISTRY = Path(os.getenv("MODEL_REGISTRY", "models/registry"))
S3_BUCKET_MODELS = os.getenv("S3_BUCKET_MODELS", "")
RUNS_DIR = Path(os.getenv("RUNS_DIR", "runs"))

# Drift
METRICS_DIR = Path(os.getenv("METRICS_DIR", "metrics"))
DRIFT_THRESHOLD_LATENCY_PCT = float(os.getenv("DRIFT_THRESHOLD_LATENCY_PCT", "20"))
DRIFT_THRESHOLD_FP_RATE = float(os.getenv("DRIFT_THRESHOLD_FP_RATE", "0.15"))

# Experiment tracking (placeholder: set MLFLOW_TRACKING_URI or WANDB_PROJECT for real)
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "")
WANDB_PROJECT = os.getenv("WANDB_PROJECT", "defense-threats")
