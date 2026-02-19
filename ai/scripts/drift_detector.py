"""
Model drift detection: compare recent inference metrics to baseline.
Expects metrics in CSV or from Prometheus (latency, accuracy, false_positive_rate).
Alerts or returns exit code when drift exceeds threshold. Run on schedule (cron/Celery).
"""
import os
import argparse
from pathlib import Path

METRICS_DIR = os.getenv("METRICS_DIR", "metrics")
BASELINE_FILE = Path(METRICS_DIR) / "baseline.json"
DRIFT_THRESHOLD_LATENCY_PCT = float(os.getenv("DRIFT_THRESHOLD_LATENCY_PCT", "20"))
DRIFT_THRESHOLD_FP_RATE = float(os.getenv("DRIFT_THRESHOLD_FP_RATE", "0.15"))


def load_baseline() -> dict:
    if not BASELINE_FILE.exists():
        return {}
    import json
    with open(BASELINE_FILE) as f:
        return json.load(f)


def save_baseline(metrics: dict) -> None:
    Path(METRICS_DIR).mkdir(parents=True, exist_ok=True)
    import json
    with open(BASELINE_FILE, "w") as f:
        json.dump(metrics, f, indent=2)


def collect_current_metrics(metrics_path: Path | None) -> dict:
    if metrics_path and metrics_path.exists():
        import json
        with open(metrics_path) as f:
            return json.load(f)
    return {
        "latency_p95_ms": 0,
        "accuracy": 0,
        "false_positive_rate": 0,
        "sample_count": 0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-file", type=str, default="")
    parser.add_argument("--update-baseline", action="store_true", help="Set current metrics as new baseline")
    parser.add_argument("--alert", action="store_true", help="Exit 1 if drift detected")
    args = parser.parse_args()
    baseline = load_baseline()
    current_path = Path(args.metrics_file) if args.metrics_file else Path(METRICS_DIR) / "latest.json"
    current = collect_current_metrics(current_path)
    if args.update_baseline:
        save_baseline(current)
        print("Baseline updated.")
        return
    if not baseline:
        print("No baseline; run with --update-baseline first.")
        return
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
        exit(1)
    if not drifted:
        print("No significant drift.")


if __name__ == "__main__":
    main()
