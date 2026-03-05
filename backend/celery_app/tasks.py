"""
Celery tasks for async work: notify on alert, backfill telemetry, trigger retrain.
Broker: REDIS_URL. Run worker with: celery -A celery_app.tasks worker -l info
"""
import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
app = Celery("defense", broker=REDIS_URL, backend=REDIS_URL)
app.conf.task_serializer = "json"
app.conf.result_serializer = "json"
app.conf.accept_content = ["json"]


@app.task
def send_alert_notification(alert_id: str, channel: str = "email") -> str:
    """Placeholder: send email or webhook for alert. Implement with SMTP or webhook URL from env."""
    # TODO: load alert from DB, resolve channel (email/webhook), send
    return f"notified {channel} for {alert_id}"


@app.task
def aggregate_telemetry_bucket(asset_id: str, bucket_ts: str) -> str:
    """Backfill or recompute one aggregation bucket. Used for catch-up or reprocessing."""
    # TODO: read raw from Kafka or S3, aggregate, write to telemetry.aggregated
    return f"aggregated {asset_id} {bucket_ts}"


@app.task
def trigger_drift_check() -> str:
    """Called by scheduler; runs drift_detector, exits 1 on drift and can enqueue retrain."""
    import subprocess
    import sys
    from pathlib import Path
    ml_drift = Path(__file__).resolve().parents[1] / "ml" / "drift_detector.py"
    if not ml_drift.exists():
        return "drift_check_skipped_no_script"
    r = subprocess.run([sys.executable, str(ml_drift), "--alert"], capture_output=True, text=True)
    if r.returncode == 1:
        trigger_retrain.delay(os.getenv("DATASET_PATH", "data/raw"), {"epochs": 30})
    return "drift_check_done"


@app.task
def trigger_retrain(dataset_path: str, config: dict) -> str:
    """Run training pipeline (train_yolo) with given config. Blocks until done."""
    import subprocess
    import sys
    from pathlib import Path
    ml_train = Path(__file__).resolve().parents[1] / "ml" / "train_yolo.py"
    if not ml_train.exists():
        return f"retrain_skipped_no_script {dataset_path}"
    epochs = config.get("epochs", 30)
    cmd = [sys.executable, str(ml_train), "--epochs", str(epochs)]
    if config.get("data_yaml"):
        cmd.extend(["--data-yaml", config["data_yaml"]])
    subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), check=False)
    return f"retrain_done {dataset_path}"
