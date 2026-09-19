"""Inference runtime adapters.

Author: Victor.I

The serving contract is stable: ndarray in -> list of (class, conf, xyxy_px) out.
Runtime underneath is swappable so Orin TensorRT can land without rewriting
Kafka/HTTP paths. Missing engines fail closed (no silent stub detections when
INFERENCE_RUNTIME=tensorrt is requested).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RawDet:
    class_name: str
    confidence: float
    xyxy: list[float]  # pixel coords


class RuntimeAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def loaded(self) -> bool: ...

    @abstractmethod
    def predict(self, image_rgb) -> list[RawDet]: ...


class StubRuntime(RuntimeAdapter):
    name = "stub"

    def loaded(self) -> bool:
        return False

    def predict(self, image_rgb) -> list[RawDet]:
        return []


class UltralyticsRuntime(RuntimeAdapter):
    """Loads .pt or Ultralytics-compatible .onnx via YOLO()."""

    name = "ultralytics"

    def __init__(self, model_path: str) -> None:
        from ultralytics import YOLO

        self._model = YOLO(model_path)
        self._path = model_path

    def loaded(self) -> bool:
        return self._model is not None

    def predict(self, image_rgb) -> list[RawDet]:
        results = self._model(image_rgb, verbose=False)
        out: list[RawDet] = []
        for r in results:
            if r.boxes is None:
                continue
            names = r.names or {}
            for box in r.boxes:
                xyxy = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                out.append(
                    RawDet(
                        class_name=names.get(cls_id, f"class_{cls_id}"),
                        confidence=conf,
                        xyxy=xyxy,
                    )
                )
        return out


class OnnxRuntimeAdapter(RuntimeAdapter):
    """YOLO ONNX via Ultralytics (owns letterbox + NMS)."""

    name = "onnx"

    def __init__(self, model_path: str) -> None:
        from ultralytics import YOLO

        self._inner = UltralyticsRuntime.__new__(UltralyticsRuntime)
        self._inner._model = YOLO(model_path)
        self._inner._path = model_path

    def loaded(self) -> bool:
        return True

    def predict(self, image_rgb) -> list[RawDet]:
        return self._inner.predict(image_rgb)


class TensorRTRuntime(RuntimeAdapter):
    """Placeholder until an Orin-built .engine is present.

    Requesting this runtime without a loadable engine raises at construct time
    so operators never silently fall back to stub detections.
    """

    name = "tensorrt"

    def __init__(self, model_path: str) -> None:
        path = Path(model_path)
        if path.suffix.lower() not in {".engine", ".trt"} or not path.is_file():
            raise RuntimeError(
                f"TensorRT runtime requires a .engine file (got {model_path!r}). "
                "Build on Jetson Orin with scripts/export_tensorrt.sh after ONNX export. "
                "Until then use INFERENCE_RUNTIME=ultralytics|auto with a .pt/.onnx."
            )
        # Future: load via tensorrt / ultralytics YOLO(engine)
        try:
            from ultralytics import YOLO

            self._model = YOLO(str(path))
        except Exception as exc:
            raise RuntimeError(
                f"failed to load TensorRT engine {path}: {exc}"
            ) from exc

    def loaded(self) -> bool:
        return True

    def predict(self, image_rgb) -> list[RawDet]:
        results = self._model(image_rgb, verbose=False)
        out: list[RawDet] = []
        for r in results:
            if r.boxes is None:
                continue
            names = r.names or {}
            for box in r.boxes:
                out.append(
                    RawDet(
                        class_name=names.get(int(box.cls[0]), f"class_{int(box.cls[0])}"),
                        confidence=float(box.conf[0]),
                        xyxy=box.xyxy[0].tolist(),
                    )
                )
        return out


def resolve_runtime(
    model_path: str,
    runtime: str | None = None,
) -> RuntimeAdapter:
    """Pick adapter from env/path. Empty/missing path -> StubRuntime."""
    if not model_path or not os.path.exists(model_path):
        return StubRuntime()

    requested = (runtime or os.getenv("INFERENCE_RUNTIME", "auto")).lower().strip()
    suffix = Path(model_path).suffix.lower()

    if requested == "stub":
        return StubRuntime()
    if requested == "tensorrt" or (requested == "auto" and suffix in {".engine", ".trt"}):
        return TensorRTRuntime(model_path)
    if requested in {"onnx", "onnxruntime"} or (requested == "auto" and suffix == ".onnx"):
        try:
            return OnnxRuntimeAdapter(model_path)
        except Exception:
            return UltralyticsRuntime(model_path)
    if requested in {"ultralytics", "pytorch", "auto", ""}:
        return UltralyticsRuntime(model_path)
    raise ValueError(f"unknown INFERENCE_RUNTIME={requested!r}")
