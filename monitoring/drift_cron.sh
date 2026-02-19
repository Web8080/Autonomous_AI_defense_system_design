#!/usr/bin/env bash
# Run drift detection; on drift exit 1 so cron can alert or trigger retrain.
cd "$(dirname "$0")/../ai/scripts"
python drift_detector.py --metrics-file ../../metrics/latest.json --alert
