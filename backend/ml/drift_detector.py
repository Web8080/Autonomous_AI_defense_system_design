"""
Model drift: compare recent inference metrics to baseline. Exit 1 if drift exceeds threshold.
Run on schedule (cron/Celery). Metrics from CSV or Prometheus/JSON (latency_p95_ms, false_positive_rate).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ml.config import METRICS_DIR, DRIFT_THRESHOLD_LATENCY_PCT, DRIFT_THRESHOLD_FP_RATE

BASELINE_FILE = METRICS_DIR / "baseline.json"


def load_baseline() -> dict:
    if not BASELINE_FILE.exists():
        return {}
    with open(BASELINE_FILE) as f:
        return json.load(f)


def save_baseline(metrics: dict) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    with open(BASELINE_FILE, "w") as f:
        json.dump(metrics, f, indent=2)


def collect_current(metrics_path: Path | None) -> dict:
    if metrics_path and metrics_path.exists():
        with open(metrics_path) as f:
            return json.load(f)
    return {"latency_p95_ms": 0, "accuracy": 0, "false_positive_rate": 0, "sample_count": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-file", type=str, default="")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--alert", action="store_true", help="Exit 1 if drift detected")
    args = parser.parse_args()

    baseline = load_baseline()
    current_path = Path(args.metrics_file) if args.metrics_file else METRICS_DIR / "latest.json"
    current = collect_current(current_path)

    if args.update_baseline:
        save_baseline(current)
        print("Baseline updated.")
        return

    if not baseline:
        print("No baseline; run with --update-baseline first.")
        sys.exit(2)

    drifted = False
    if baseline.get("latency_p95_ms") and current.get("latency_p95_ms"):
        pct = (current["latency_p95_ms"] - baseline["latency_p95_ms"]) / baseline["latency_p95_ms"] * 100
        if pct > DRIFT_THRESHOLD_LATENCY_PCT:
            print(f"Latency drift: +{pct:.1f}%")
            drifted = True
    if baseline.get("false_positive_rate") is not None and current.get("false_positive_rate") is not None:
        if current["false_positive_rate"] > baseline["false_positive_rate"] + DRIFT_THRESHOLD_FP_RATE:
            print("False positive rate drift")
            drifted = True

    if args.alert and drifted:
        sys.exit(1)
    if not drifted:
        print("No significant drift.")


if __name__ == "__main__":
    main()
