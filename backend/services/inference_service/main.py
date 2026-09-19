"""
Production inference service: YOLO threat detection on frames from Kafka or HTTP.

Author: Victor.I

- Runtime adapters: ultralytics (.pt/.onnx), onnxruntime, tensorrt (.engine) stub.
- Kafka: consume inference.frames -> infer -> produce inference.detections.
- HTTP: /infer, /infer/batch; Prometheus /metrics; provenance stamps.
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

from runtimes import RuntimeAdapter, StubRuntime, resolve_runtime

INFERENCE_TOPIC = os.getenv("INFERENCE_DETECTIONS_TOPIC", "inference.detections")
FRAMES_TOPIC = os.getenv("INFERENCE_FRAMES_TOPIC", "inference.frames")
MODEL_PATH = os.getenv("MODEL_PATH", "")
INFERENCE_RUNTIME = os.getenv("INFERENCE_RUNTIME", "auto")
MAX_IMAGE_B64_BYTES = int(os.getenv("MAX_IMAGE_B64_BYTES", "10485760"))
MAX_DETECTIONS_PER_FRAME = int(os.getenv("MAX_DETECTIONS_PER_FRAME", "50"))
MAX_BATCH_FRAMES = int(os.getenv("MAX_BATCH_FRAMES", "20"))
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://ml-service:8000")

runtime: RuntimeAdapter = StubRuntime()
_model_meta: dict | None = None
_kafka_consumer_task = None
_shutdown = False

_metrics = {
    "inference_requests_total": 0,
    "inference_latency_sum_ms": 0.0,
    "inference_errors_total": 0,
    "inference_stage_preprocess_ms": 0.0,
    "inference_stage_infer_ms": 0.0,
    "inference_stage_postprocess_ms": 0.0,
}


def load_model():
    global runtime
    try:
        runtime = resolve_runtime(MODEL_PATH, INFERENCE_RUNTIME)
        print(
            f"inference: runtime={runtime.name} loaded={runtime.loaded()} "
            f"path={MODEL_PATH!r}"
        )
    except Exception as exc:
        print(f"inference: failed to load runtime ({exc}); staying stub")
        runtime = StubRuntime()


def sync_production_meta():
    global _model_meta
    try:
        import httpx

        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{ML_SERVICE_URL}/models/production")
            if r.status_code != 200:
                raise LookupError(f"registry: {r.status_code} {r.text[:200]}")
            data = r.json()
            _model_meta = {"model_id": data["id"], "model_version": data["version"]}
            # Prefer registry artifact when MODEL_PATH unset
            uri = data.get("artifact_uri") or data.get("artifact_path")
            if uri and (not MODEL_PATH or not os.path.exists(MODEL_PATH)):
                local = uri.replace("file://", "")
                if os.path.exists(local):
                    os.environ["MODEL_PATH"] = local
    except Exception as exc:
        _model_meta = None
        print(f"inference: production model lookup failed (will stamp 'unregistered'): {exc}")


def _model_stamp() -> dict:
    if _model_meta:
        return {"model_id": _model_meta["model_id"], "model_version": _model_meta["model_version"]}
    return {"model_id": None, "model_version": "unregistered"}


def _is_url_safe(url: str) -> bool:
    try:
        from defense_shared.security import is_url_safe_for_fetch

        return is_url_safe_for_fetch(url)
    except ImportError:
        return False


def _decode_image(image_b64: str | None, image_url: str | None):
    import io

    import numpy as np
    from PIL import Image

    if image_b64:
        raw = base64.b64decode(image_b64, validate=True)
        if len(raw) > MAX_IMAGE_B64_BYTES:
            raise HTTPException(status_code=400, detail="image_b64 exceeds max size")
        return np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
    if image_url and _is_url_safe(image_url):
        import urllib.request

        with urllib.request.urlopen(image_url, timeout=10) as r:
            raw = r.read()
        return np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
    return None


def _run_inference(
    image_b64: str | None,
    image_url: str | None,
    asset_id: str,
    frame_id: str,
    timestamp: str,
) -> list[dict]:
    global _metrics
    t0 = time.perf_counter()
    try:
        if not runtime.loaded():
            _metrics["inference_requests_total"] += 1
            _metrics["inference_latency_sum_ms"] += (time.perf_counter() - t0) * 1000
            return [
                {
                    "asset_id": asset_id,
                    "frame_id": frame_id,
                    "timestamp": timestamp,
                    "class_name": "stub_threat",
                    "confidence": 0.0,
                    "threat_score": 0.0,
                    "bbox": [0.1, 0.1, 0.3, 0.3],
                    "model_version": "stub",
                    "metadata": {"provenance": "stub", "runtime": runtime.name},
                }
            ]

        t_pre = time.perf_counter()
        img = _decode_image(image_b64, image_url)
        pre_ms = (time.perf_counter() - t_pre) * 1000
        if img is None:
            raise ValueError("No image source")

        t_inf = time.perf_counter()
        raw_dets = runtime.predict(img)
        inf_ms = (time.perf_counter() - t_inf) * 1000

        t_post = time.perf_counter()
        out: list[dict] = []
        stamp = _model_stamp()
        frame_h = max(int(img.shape[0]), 1)
        frame_w = max(int(img.shape[1]), 1)
        for det in raw_dets[:MAX_DETECTIONS_PER_FRAME]:
            xyxy = det.xyxy
            norm = [
                max(min(xyxy[0] / frame_w, 1.0), 0.0),
                max(min(xyxy[1] / frame_h, 1.0), 0.0),
                max(min(xyxy[2] / frame_w, 1.0), 0.0),
                max(min(xyxy[3] / frame_h, 1.0), 0.0),
            ]
            provenance = "production" if stamp.get("model_id") else "unregistered"
            out.append(
                {
                    "asset_id": asset_id,
                    "frame_id": frame_id,
                    "timestamp": timestamp,
                    "class_name": det.class_name,
                    "confidence": det.confidence,
                    "threat_score": det.confidence,
                    "bbox": [round(v, 5) for v in norm],
                    "model_version": stamp.get("model_version", "unregistered"),
                    "metadata": {
                        "bbox_px": [round(v, 3) for v in xyxy],
                        "provenance": provenance,
                        "runtime": runtime.name,
                        **({"model_id": stamp["model_id"]} if stamp.get("model_id") else {}),
                    },
                }
            )
        post_ms = (time.perf_counter() - t_post) * 1000

        _metrics["inference_requests_total"] += 1
        _metrics["inference_latency_sum_ms"] += (time.perf_counter() - t0) * 1000
        _metrics["inference_stage_preprocess_ms"] += pre_ms
        _metrics["inference_stage_infer_ms"] += inf_ms
        _metrics["inference_stage_postprocess_ms"] += post_ms
        return out
    except HTTPException:
        raise
    except Exception as e:
        _metrics["inference_errors_total"] += 1
        raise HTTPException(status_code=500, detail=str(e))


def _kafka_consumer_loop():
    global _shutdown
    if not KAFKA_BOOTSTRAP:
        return
    try:
        import json

        from kafka import KafkaConsumer, KafkaProducer

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
                    detections = _run_inference(
                        f.get("image_b64"),
                        f.get("image_url"),
                        f.get("asset_id", ""),
                        f.get("frame_id", ""),
                        f.get("timestamp", ""),
                    )
                    producer.send(
                        INFERENCE_TOPIC,
                        value={"detections": detections, "frame_id": f.get("frame_id", "")},
                    )
            except Exception:
                _metrics["inference_errors_total"] += 1
        consumer.close()
        producer.close()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync_production_meta()
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
    return {
        "status": "ok",
        "service": "inference-service",
        "model_loaded": runtime.loaded(),
        "runtime": runtime.name,
        "model_path": MODEL_PATH or None,
    }


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
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
        "# HELP inference_stage_preprocess_ms Preprocess stage ms sum.",
        "# TYPE inference_stage_preprocess_ms counter",
        f"inference_stage_preprocess_ms {_metrics['inference_stage_preprocess_ms']}",
        "# HELP inference_stage_infer_ms Infer stage ms sum.",
        "# TYPE inference_stage_infer_ms counter",
        f"inference_stage_infer_ms {_metrics['inference_stage_infer_ms']}",
        "# HELP inference_stage_postprocess_ms Postprocess stage ms sum.",
        "# TYPE inference_stage_postprocess_ms counter",
        f"inference_stage_postprocess_ms {_metrics['inference_stage_postprocess_ms']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.post("/infer")
async def infer(body: InferenceRequest) -> dict:
    if not body.image_b64 and not body.image_url:
        raise HTTPException(status_code=400, detail="image_b64 or image_url required")
    if body.image_url and not _is_url_safe(body.image_url):
        raise HTTPException(status_code=400, detail="image_url not allowed (SSRF policy)")
    detections = _run_inference(
        body.image_b64,
        body.image_url,
        body.asset_id,
        body.frame_id,
        body.timestamp,
    )
    return {"detections": detections, "frame_id": body.frame_id, "runtime": runtime.name}


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
        detections = _run_inference(
            f.get("image_b64"),
            f.get("image_url"),
            f.get("asset_id", ""),
            f.get("frame_id", ""),
            f.get("timestamp", ""),
        )
        results.append({"frame_id": f.get("frame_id", ""), "detections": detections})
    return {"results": results, "runtime": runtime.name}


def _graceful_shutdown(signum, frame):
    global _shutdown
    _shutdown = True


signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)
