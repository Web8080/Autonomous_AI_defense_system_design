"""
Export trained YOLO to ONNX or TorchScript for production inference.
Output: model.pt (TorchScript) or model.onnx. Inference service can load either.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ml.config import MODEL_REGISTRY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", type=str, required=True, help="best.pt or last.pt")
    parser.add_argument("--format", type=str, default="onnx", choices=["onnx", "torchscript"])
    parser.add_argument("--output-dir", type=str, default=str(MODEL_REGISTRY))
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--half", action="store_true", help="FP16 for ONNX")
    args = parser.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        print("Install ultralytics: pip install ultralytics")
        sys.exit(1)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    model = YOLO(args.weights)

    if args.format == "onnx":
        path = model.export(format="onnx", imgsz=args.imgsz, half=args.half)
        out = Path(args.output_dir) / "model.onnx"
        Path(path).rename(out)
        print("Exported:", out)
    else:
        path = model.export(format="torchscript", imgsz=args.imgsz)
        out = Path(args.output_dir) / "model.pt"
        Path(path).rename(out)
        print("Exported:", out)


if __name__ == "__main__":
    main()
