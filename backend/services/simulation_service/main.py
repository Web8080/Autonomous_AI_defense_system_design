"""Simulation lab service.

Runs exercises that drive the *real product* pipeline with real camera frames:

- Layer 1: real-footage replay (frozen VisDrone temporal corpus) at nominal fps
- Layer 2: synthetic scene composition (procedural aerial world, exact GT)
- Layer 3: live ingest (dashboard renders a 3D world; frames enter here)

All three publish to the same Kafka `inference.frames` topic a physical camera
gateway would, so the trained model, detection persistence, and alert services
treat the feed as production. What they produce flows back on
`inference.detections` and is scored live against the exercise ground truth.
"""
from __future__ import annotations

import base64
import binascii
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from engine import Exercise, ExerciseManager, FrameRec
from frames_l1 import CorpusSource
from frames_l2 import SCENARIOS, SyntheticSource
from scoring import GTBox
from transport import KafkaTransport

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CORPUS_MANIFEST = os.getenv(
    "SIM_CORPUS_MANIFEST", "data/visdrone-yolo/corpus/corpus.json"
)
MAX_EXERCISES = 8
L3_CAP = 4096


class ExerciseCreate(BaseModel):
    layer: int = Field(ge=1, le=3)
    scenario: str = "railway-yard"
    fps: float = Field(default=10.0, gt=0, le=30)
    sequence: Optional[str] = None
    start_offset: int = 0
    frames: Optional[int] = Field(default=None, gt=0)


class IngestFrame(BaseModel):
    image_b64: str
    gt: list[dict] = Field(default_factory=list, description="[{class_name, bbox: [x0,y0,x1,y1] normalized}]")
    width: int = Field(default=640, gt=0)
    height: int = Field(default=360, gt=0)


manager: ExerciseManager


def _l1_provider(manifest: str, sequence: Optional[str], start_offset: int, frames: Optional[int]):
    src = CorpusSource(manifest_path=manifest, sequence=sequence, start_offset=start_offset, frames_limit=frames)
    src.load()

    def provider(idx: int):
        b64, w, h, gts = src.frame(idx)
        return b64, w, h, gts

    return provider, src.meta, src.sequence_ids, len(src)


def _l2_provider(scenario: str, fps: float):
    spec = SCENARIOS.get(scenario)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown layer2 scenario '{scenario}'; available: {sorted(SCENARIOS)}")
    src = SyntheticSource(spec=spec, fps=fps)

    def provider(idx: int):
        return src.frame(idx)

    return provider, src.meta, None, None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global manager
    transport = KafkaTransport(KAFKA_BOOTSTRAP)
    manager = ExerciseManager(transport)
    yield
    transport.close()


app = FastAPI(title="Defense Simulation Lab", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/layers")
def layers() -> dict:
    available = Path(CORPUS_MANIFEST).exists()
    seqs: list[str] = []
    if available:
        try:
            src = CorpusSource(manifest_path=CORPUS_MANIFEST)
            seqs = src.sequence_ids  # type: ignore[attr-defined]
        except Exception:
            seqs = []
    return {
        "1": {"name": "real-footage replay", "scenario": "visdrone-corpus", "available": available, "sequences": seqs, "frames_hint": 548},
        "2": {"name": "synthetic compose", "scenarios": sorted(SCENARIOS)},
        "3": {"name": "live ingest (3D world)", "scenario": "browser-world"},
    }


@app.post("/exercises", status_code=201)
def create_exercise(body: ExerciseCreate) -> dict:
    if len(manager.exercises) >= MAX_EXERCISES:
        raise HTTPException(status_code=429, detail="too many exercises; stop one first")
    if body.layer == 1:
        if not Path(CORPUS_MANIFEST).exists():
            raise HTTPException(
                status_code=400,
                detail="corpus manifest not found; build it with backend/ml/build_corpus.py",
            )
        provider, meta, _, total = _l1_provider(
            CORPUS_MANIFEST, body.sequence, body.start_offset, body.frames
        )
        frames_total = body.frames or total
        scenario = body.sequence or "visdrone-corpus"
        fps = body.fps
        if body.sequence:
            seq_provider, meta, _, seq_total = _l1_provider(
                CORPUS_MANIFEST, body.sequence, body.start_offset, body.frames
            )
            provider = seq_provider
            frames_total = body.frames or seq_total
    elif body.layer == 2:
        provider, meta, _, total = _l2_provider(body.scenario, body.fps)
        fps = body.fps
        frames_total = body.frames or 30 * 60 * 5
        scenario = body.scenario
    else:
        provider = None
        fps = body.fps
        frames_total = None
        meta = lambda idx: {"source": "l3-ingest", "frame": idx}  # noqa: E731
        scenario = "l3-ingest"

    ex = manager.start(
        layer=body.layer,
        scenario=scenario,
        fps=fps,
        provider=provider or (lambda idx: ("", 0, 0, [])),
        frames_total=frames_total if body.layer != 3 else None,
        meta=meta,
    )
    return ex.status()


@app.get("/exercises")
def list_exercises() -> list[dict]:
    return [_summary(ex) for ex in sorted(manager.exercises.values(), key=lambda e: e.created_ms, reverse=True)]


def _summary(ex: Exercise) -> dict:
    return {
        "id": ex.id,
        "layer": ex.layer,
        "scenario": ex.scenario,
        "state": ex.state,
        "fps": ex.fps,
        "frame_index": ex.frame_index,
        "frames_total": ex.frames_total,
        "alerts_candidates": ex.tally.alert_candidates,
        "precision": round(ex.tally.precision, 3),
        "recall": round(ex.tally.recall, 3),
    }


@app.get("/exercises/{exercise_id}")
def exercise_status(exercise_id: str) -> dict:
    ex = manager.exercises.get(exercise_id)
    if not ex:
        raise HTTPException(status_code=404, detail="exercise not found")
    return ex.status()


@app.post("/exercises/{exercise_id}/stop")
def stop_exercise(exercise_id: str) -> dict:
    if not manager.stop(exercise_id):
        raise HTTPException(status_code=404, detail="exercise not found")
    return {"ok": True, "id": exercise_id}


@app.post("/exercises/{exercise_id}/ingest", status_code=201)
def ingest_frame(exercise_id: str, body: IngestFrame) -> dict:
    ex = manager.exercises.get(exercise_id)
    if not ex:
        raise HTTPException(status_code=404, detail="exercise not found")
    if ex.layer != 3:
        raise HTTPException(status_code=400, detail="ingest is for layer 3 exercises only")
    try:
        base64.b64decode(body.image_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="image_b64 must be valid base64")
    idx = ex.frame_index
    if idx >= L3_CAP:
        raise HTTPException(status_code=429, detail="layer3 cap reached; stop and start a new exercise")
    gts = [
        GTBox(class_name=str(g.get("class_name", "unknown")), bbox=[float(v) for v in g["bbox"]])
        for g in body.gt
        if g.get("bbox") and len(g["bbox"]) == 4
    ]
    frame_id = f"{ex.id}:{idx}"
    ex.record_frame(
        frame_id, gts, FrameRec(frame_id=frame_id, image_b64=body.image_b64, width=body.width, height=body.height, index=idx)
    )
    msg = {
        "asset_id": f"sim-3",
        "frame_id": frame_id,
        "timestamp": _now_iso(),
        "image_b64": body.image_b64,
        "source": "simulation/l3",
        "camera": "browser-world",
    }
    try:
        manager.transport.send_frame(msg)
    except RuntimeError as exc:
        ex.last_error = str(exc)
        raise HTTPException(status_code=503, detail=str(exc))
    return {"frame_id": frame_id, "queue_depth": ex.frame_index}


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()