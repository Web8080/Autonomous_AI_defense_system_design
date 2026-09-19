#!/usr/bin/env bash
# Build TensorRT engine on Jetson Orin (hardware-gated).
# Author: Victor.I
#
# Prerequisites (on Orin): TensorRT, CUDA, exported ONNX from backend/ml/export_model.py,
# optional INT8 calib list from backend/ml/carve_int8_calib.py.
set -euo pipefail

ONNX="${1:-data/models/model.onnx}"
OUT="${2:-data/models/visdrone-yolov8n.engine}"
PRECISION="${PRECISION:-fp16}"  # fp16 | int8
CALIB="${CALIB:-data/calib/visdrone-int8.txt}"

if [[ ! -f "$ONNX" ]]; then
  echo "missing ONNX: $ONNX — run: python -m ml.export_model --weights data/models/visdrone-yolov8n.pt" >&2
  exit 1
fi

if ! command -v trtexec >/dev/null 2>&1; then
  echo "trtexec not found. This script must run on a Jetson/Orin image with TensorRT." >&2
  echo "Lab path: keep INFERENCE_RUNTIME=ultralytics with .pt until Orin is available." >&2
  exit 2
fi

ARGS=(--onnx="$ONNX" --saveEngine="$OUT" --workspace=4096)
if [[ "$PRECISION" == "fp16" ]]; then
  ARGS+=(--fp16)
elif [[ "$PRECISION" == "int8" ]]; then
  ARGS+=(--int8)
  if [[ -f "$CALIB" ]]; then
    ARGS+=(--calib="$CALIB")
  else
    echo "warn: INT8 without calib list $CALIB" >&2
  fi
else
  echo "unknown PRECISION=$PRECISION" >&2
  exit 1
fi

echo "building engine: ${ARGS[*]}"
trtexec "${ARGS[@]}"
echo "wrote $OUT — register as runtime=tensorrt precision=$PRECISION and re-certify on-device"
