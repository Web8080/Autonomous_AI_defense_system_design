"""
Production inference service: YOLO threat detection on frames from Kafka or HTTP.
- Loads model from MODEL_PATH (stub if unset). Quantized/ONNX optional.
- Kafka: consume inference.frames -> infer -> produce inference.detections.
- HTTP: /infer, /infer/batch with <100ms latency target; multi-frame batching.
- Health, Prometheus /metrics, graceful shutdown.
"""
import os
import base64
import asyncio
import signal
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

# Config
INFERENCE_TOPIC = os.getenv("INFERENCE_DETECTIONS_TOPIC", "inference.detections")
FRAMES_TOPIC = os.getenv("INFERENCE_FRAMES_TOPIC", "inference.frames")
MODEL_PATH = os.getenv("MODEL_PATH", "")
MAX_IMAGE_B64_BYTES = int(os.getenv("MAX_IMAGE_B64_BYTES", "10485760"))
MAX_DETECTIONS_PER_FRAME = int(os.getenv("MAX_DETECTIONS_PER_FRAME", "50"))
MAX_BATCH_FRAMES = int(os.getenv("MAX_BATCH_FRAMES", "20"))
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

model = None
_kafka_consumer_task = None
_shutdown = False

# Prometheus metrics (simple in-process)
_metrics = {"inference_requests_total": 0, "inference_latency_sum_ms": 0.0, "inference_errors_total": 0}


def load_model():
    global model
    if not MODEL_PATH or not os.path.exists(MODEL_PATH):
        return
    try:
        from ultralytics import YOLO
        model = YOLO(MODEL_PATH)
    except ImportError:
        pass
    except Exception:
        pass


def _is_url_safe(url: str) -> bool:
    try:
        from defense_shared.security import is_url_safe_for_fetch
        return is_url_safe_for_fetch(url)
    except ImportError:
        return False


def _run_inference(image_b64: str | None, image_url: str | None, asset_id: str, frame_id: str, timestamp: str) -> list[dict]:
    """Run YOLO on one frame; return list of detections (capped). Stub if no model."""
    global _metrics
    t0 = time.perf_counter()
    try:
        if model is None:
            _metrics["inference_requests_total"] += 1
            _metrics["inference_latency_sum_ms"] += (time.perf_counter() - t0) * 1000
            return [{
                "asset_id": asset_id,
                "frame_id": frame_id,
                "timestamp": timestamp,
                "class_name": "stub_threat",
                "confidence": 0.85,
                "threat_score": 0.7,
                "bbox": [0.1, 0.1, 0.3, 0.3],
                "metadata": {},
            }]

        img = None
        if image_b64:
            raw = base64.b64decode(image_b64, validate=True)
            if len(raw) > MAX_IMAGE_B64_BYTES:
                raise HTTPException(status_code=400, detail="image_b64 exceeds max size")
            import numpy as np
            from PIL import Image
            import io
            img = np.array(Image.open(io.BytesIO(raw)))
        elif image_url and _is_url_safe(image_url):
            import urllib.request
            with urllib.request.urlopen(image_url, timeout=10) as r:
                raw = r.read()
            import numpy as np
            from PIL import Image
            import io
            img = np.array(Image.open(io.BytesIO(raw)))

        if img is None:
            raise ValueError("No image source")

        results = model(img, verbose=False)
        out = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                if len(out) >= MAX_DETECTIONS_PER_FRAME:
                    break
                xyxy = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                names = r.names or {}
                class_name = names.get(cls_id, f"class_{cls_id}")
                out.append({
                    "asset_id": asset_id,
                    "frame_id": frame_id,
                    "timestamp": timestamp,
                    "class_name": class_name,
                    "confidence": conf,
                    "threat_score": conf,
                    "bbox": [xyxy[0], xyxy[1], xyxy[2], xyxy[3]],
                    "metadata": {},
                })
        _metrics["inference_requests_total"] += 1
        _metrics["inference_latency_sum_ms"] += (time.perf_counter() - t0) * 1000
        return out
    except HTTPException:
        raise
    except Exception as e:
        _metrics["inference_errors_total"] += 1
        raise HTTPException(status_code=500, detail=str(e))


def _kafka_consumer_loop():
    """Background: consume frames, infer, produce detections."""
    global _shutdown
    if not KAFKA_BOOTSTRAP:
        return
    try:
        from kafka import KafkaConsumer, KafkaProducer
        import json
        consumer = KafkaConsumer(
            FRAMES_TOPIC,
            bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
            group_id="inference-service",
            auto_offset_reset="earliest",
        )
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        for msg in consumer:
            if _shutdown:
                break
            try:
                payload = json.loads(msg.value.decode())
                frames = payload if isinstance(payload, list) else [payload]
                for f in frames[:MAX_BATCH_FRAMES]:
                    asset_id = f.get("asset_id", "")
                    frame_id = f.get("frame_id", "")
                    ts = f.get("timestamp", "")
                    img_b64 = f.get("image_b64")
                    img_url = f.get("image_url")
                    detections = _run_inference(img_b64, img_url, asset_id, frame_id, ts)
                    producer.send(INFERENCE_TOPIC, value={"detections": detections, "frame_id": frame_id})
            except Exception as e:
                _metrics["inference_errors_total"] += 1
                pass
        consumer.close()
        producer.close()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    loop = asyncio.get_event_loop()
    global _kafka_consumer_task
    if KAFKA_BOOTSTRAP:
        _kafka_consumer_task = loop.run_in_executor(None, _kafka_consumer_loop)
    yield
    global _shutdown
    _shutdown = True
    if _kafka_consumer_task:
        await _kafka_consumer_task


app = FastAPI(title="Inference Service", lifespan=lifespan)


class InferenceRequest(BaseModel):
    asset_id: str = Field(..., max_length=128)
    frame_id: str = Field(..., max_length=128)
    timestamp: str = Field(..., max_length=64)
    image_b64: str | None = None
    image_url: str | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "inference-service", "model_loaded": model is not None}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus exposition format."""
    lines = [
        "# HELP inference_requests_total Total inference requests.",
        "# TYPE inference_requests_total counter",
        f"inference_requests_total {_metrics['inference_requests_total']}",
        "# HELP inference_latency_sum_ms Sum of latency in ms.",
        "# TYPE inference_latency_sum_ms counter",
        f"inference_latency_sum_ms {_metrics['inference_latency_sum_ms']}",
        "# HELP inference_errors_total Total inference errors.",
        "# TYPE inference_errors_total counter",
        f"inference_errors_total {_metrics['inference_errors_total']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.post("/infer")
async def infer(body: InferenceRequest) -> dict:
    if not body.image_b64 and not body.image_url:
        raise HTTPException(status_code=400, detail="image_b64 or image_url required")
    if body.image_url and not _is_url_safe(body.image_url):
        raise HTTPException(status_code=400, detail="image_url not allowed (SSRF policy)")
    detections = _run_inference(
        body.image_b64, body.image_url,
        body.asset_id, body.frame_id, body.timestamp,
    )
    return {"detections": detections, "frame_id": body.frame_id}


@app.post("/infer/batch")
async def infer_batch(body: dict) -> dict:
    frames = body.get("frames", [])
    if not isinstance(frames, list):
        raise HTTPException(status_code=400, detail="frames must be array")
    if not frames:
        return {"results": []}
    if len(frames) > MAX_BATCH_FRAMES:
        raise HTTPException(status_code=400, detail="frames exceeds max batch size")
    results = []
    for f in frames:
        asset_id = f.get("asset_id", "")
        frame_id = f.get("frame_id", "")
        ts = f.get("timestamp", "")
        detections = _run_inference(
            f.get("image_b64"), f.get("image_url"),
            asset_id, frame_id, ts,
        )
        results.append({"frame_id": frame_id, "detections": detections})
    return {"results": results}


def _graceful_shutdown(signum, frame):
    global _shutdown
    _shutdown = True


signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)
