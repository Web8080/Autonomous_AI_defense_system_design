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
    """Called by scheduler; checks inference metrics and may enqueue retrain. See Phase 3 drift scripts."""
    # TODO: call drift detection script or API
    return "drift_check_done"


@app.task
def trigger_retrain(dataset_path: str, config: dict) -> str:
    """Enqueue training job. Actual training runs in Phase 3 scripts."""
    # TODO: invoke training pipeline (subprocess or k8s job)
    return f"retrain_queued {dataset_path}"
