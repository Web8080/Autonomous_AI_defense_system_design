#!/usr/bin/env bash
# Extended VisDrone fine-tune for lab simulation consumption.
# Author: Victor.I
#
# Continues from the existing stage-2 weights with more epochs / stronger
# augmentation path. Does NOT auto-promote — eval gate still fail-closed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WEIGHTS="${WEIGHTS:-data/models/visdrone-yolov8n.pt}"
OUT="${OUT:-data/models/visdrone-yolov8n-ext.pt}"
EPOCHS="${EPOCHS:-80}"
BATCH="${BATCH:-8}"
DEVICE="${DEVICE:-mps}"
VENV="${VENV:-.venv-ml}"

if [[ ! -f "$WEIGHTS" ]]; then
  echo "missing weights: $WEIGHTS" >&2
  exit 1
fi

# Prefer project ML venv when present
if [[ -x "$VENV/bin/python" ]]; then
  PY="$VENV/bin/python"
elif [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

export PYTHONPATH="$ROOT/backend:${PYTHONPATH:-}"
mkdir -p data/models runs/detect

echo "training extended run: epochs=$EPOCHS device=$DEVICE weights=$WEIGHTS -> $OUT"
"$PY" -m ml.train \
  --dataset-root data/visdrone-yolo/train \
  --benchmark-root data/visdrone-yolo/val \
  --artifact-out "$OUT" \
  --weights "$WEIGHTS" \
  --epochs "$EPOCHS" \
  --batch "$BATCH" \
  --imgsz 640 \
  --device "$DEVICE" \
  --project runs/detect \
  --run-name visdrone-ext \
  --git-sha "$(git rev-parse --short HEAD 2>/dev/null || echo local)" \
  --lr0 0.01

echo "done. To use in lab without promote:"
echo "  MODEL_PATH=/models/$(basename "$OUT")  (compose mounts data/models)"
echo "Then re-eval: python -m ml.eval --weights $OUT ..."
