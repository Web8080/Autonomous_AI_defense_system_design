"""
Inference service: run YOLO/PyTorch threat detection on frames.
Safeguardrails: input size limits, SSRF-safe URL allowlist, output cap.
"""
import os
import base64
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Optional: uncomment when real model is mounted
# import torch
# from ultralytics import YOLO

INFERENCE_TOPIC = os.getenv("INFERENCE_DETECTIONS_TOPIC", "inference.detections")
MODEL_PATH = os.getenv("MODEL_PATH", "")
MAX_IMAGE_B64_BYTES = int(os.getenv("MAX_IMAGE_B64_BYTES", "10485760"))
MAX_DETECTIONS_PER_FRAME = int(os.getenv("MAX_DETECTIONS_PER_FRAME", "50"))
MAX_BATCH_FRAMES = int(os.getenv("MAX_BATCH_FRAMES", "20"))
model = None


def load_model():
    global model
    if MODEL_PATH and os.path.exists(MODEL_PATH):
        # model = YOLO(MODEL_PATH)
        pass
    # else: stub mode


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    load_model()
    yield


app = FastAPI(title="Inference Service", lifespan=lifespan)


def _is_url_safe(url: str) -> bool:
    try:
        from defense_shared.security import is_url_safe_for_fetch
        return is_url_safe_for_fetch(url)
    except ImportError:
        return False


class InferenceRequest(BaseModel):
    asset_id: str = Field(..., max_length=128)
    frame_id: str = Field(..., max_length=128)
    timestamp: str = Field(..., max_length=64)
    image_b64: str | None = None
    image_url: str | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "inference-service", "model_loaded": model is not None}


@app.post("/infer")
async def infer(body: InferenceRequest) -> dict:
    """Run detection on one frame. Input validated for size and SSRF (image_url)."""
    if not body.image_b64 and not body.image_url:
        raise HTTPException(status_code=400, detail="image_b64 or image_url required")
    if body.image_b64:
        try:
            raw = base64.b64decode(body.image_b64, validate=True)
            if len(raw) > MAX_IMAGE_B64_BYTES:
                raise HTTPException(status_code=400, detail="image_b64 exceeds max size")
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=400, detail="Invalid image_b64")
    if body.image_url and not _is_url_safe(body.image_url):
        raise HTTPException(status_code=400, detail="image_url not allowed (SSRF policy)")
    # TODO: decode image, run model, map to threat classes
    # if model: results = model(...); map to Detection schema; cap len to MAX_DETECTIONS_PER_FRAME
    detections = [
        {
            "asset_id": body.asset_id,
            "frame_id": body.frame_id,
            "timestamp": body.timestamp,
            "class_name": "stub_threat",
            "confidence": 0.85,
            "threat_score": 0.7,
            "bbox": [0.1, 0.1, 0.3, 0.3],
            "metadata": {},
        }
    ]
    # TODO: produce to Kafka inference.detections
    return {"detections": detections, "frame_id": body.frame_id}


@app.post("/infer/batch")
async def infer_batch(body: dict) -> dict:
    """Batch of frames. Capped at MAX_BATCH_FRAMES. Stub returns list of stub detections."""
    frames = body.get("frames", [])
    if not isinstance(frames, list):
        raise HTTPException(status_code=400, detail="frames must be array")
    if not frames:
        return {"results": []}
    if len(frames) > MAX_BATCH_FRAMES:
        raise HTTPException(status_code=400, detail="frames exceeds max batch size")
    results = []
    for f in frames[:MAX_BATCH_FRAMES]:
        results.append({
            "frame_id": f.get("frame_id", ""),
            "detections": [{"class_name": "stub_threat", "confidence": 0.8, "threat_score": 0.6, "bbox": [0, 0, 0.1, 0.1]}],
        })
    return {"results": results}
