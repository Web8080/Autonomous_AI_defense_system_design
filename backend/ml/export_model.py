"""
Export trained YOLO to ONNX or TorchScript for production / Orin TRT input.

Author: Victor.I

Output: model.onnx or TorchScript. Inference adapters load .pt/.onnx today;
TensorRT .engine is built on Orin via scripts/export_tensorrt.sh.
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
    parser.add_argument("--output-name", type=str, default="", help="override filename stem")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--half", action="store_true", help="FP16 for ONNX")
    parser.add_argument("--simplify", action="store_true", help="simplify ONNX graph")
    args = parser.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        print("Install ultralytics: pip install ultralytics")
        sys.exit(1)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    model = YOLO(args.weights)
    stem = args.output_name or Path(args.weights).stem

    if args.format == "onnx":
        path = model.export(
            format="onnx",
            imgsz=args.imgsz,
            half=args.half,
            simplify=args.simplify,
        )
        out = Path(args.output_dir) / f"{stem}.onnx"
        Path(path).replace(out)
        print("Exported:", out)
        print(
            "Next (on Orin): PRECISION=fp16 ./scripts/export_tensorrt.sh",
            out,
            f"data/models/{stem}.engine",
        )
    else:
        path = model.export(format="torchscript", imgsz=args.imgsz)
        out = Path(args.output_dir) / f"{stem}.torchscript.pt"
        Path(path).replace(out)
        print("Exported:", out)


if __name__ == "__main__":
    main()
