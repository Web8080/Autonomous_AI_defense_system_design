"""
Experiment tracking placeholder: MLflow or Weights & Biases.
Set MLFLOW_TRACKING_URI or WANDB_PROJECT to enable. Used by train_yolo/finetune.
"""
from __future__ import annotations

import os
from typing import Any


def log_params(params: dict[str, Any]) -> None:
    uri = os.getenv("MLFLOW_TRACKING_URI")
    if uri:
        try:
            import mlflow
            mlflow.set_tracking_uri(uri)
            for k, v in params.items():
                mlflow.log_param(k, v)
        except Exception:
            pass
    if os.getenv("WANDB_PROJECT"):
        try:
            import wandb
            wandb.log(params)
        except Exception:
            pass


def log_metrics(metrics: dict[str, float], step: int | None = None) -> None:
    if os.getenv("MLFLOW_TRACKING_URI"):
        try:
            import mlflow
            mlflow.log_metrics(metrics, step=step)
        except Exception:
            pass
    if os.getenv("WANDB_PROJECT"):
        try:
            import wandb
            wandb.log(metrics, step=step)
        except Exception:
            pass


def log_artifact(local_path: str, artifact_path: str = "model") -> None:
    if os.getenv("MLFLOW_TRACKING_URI"):
        try:
            import mlflow
            mlflow.log_artifact(local_path, artifact_path=artifact_path)
        except Exception:
            pass
