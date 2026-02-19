"""
Trigger retrain or fine-tune when drift is detected. Writes job request to queue or runs train script.
For local: subprocess to train_yolo.py. For cloud: enqueue Celery task or K8s Job.
"""
import os
import subprocess
import sys
from pathlib import Path

# Optional: from celery_app.tasks import trigger_retrain
TRAIN_SCRIPT = Path(__file__).resolve().parent / "train_yolo.py"
DATASET_PATH = os.getenv("DATASET_PATH", "data/raw")


def main():
    if not TRAIN_SCRIPT.exists():
        print("Train script not found.")
        sys.exit(1)
    use_celery = os.getenv("RETRAIN_VIA_CELERY", "").lower() == "true"
    if use_celery:
        # trigger_retrain.delay(DATASET_PATH, {"epochs": 30})
        print("Celery retrain enqueued (uncomment in script).")
    else:
        subprocess.run([sys.executable, str(TRAIN_SCRIPT), "--epochs", "30"], check=False)
    print("Retrain triggered.")


if __name__ == "__main__":
    main()
