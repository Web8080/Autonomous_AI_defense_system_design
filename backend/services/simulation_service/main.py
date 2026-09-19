"""Simulation lab service.

Author: Victor.I

Exercises that drive the *real product* pipeline with camera frames:

- Layer 1: real aerial footage — VisDrone corpus stills OR MP4 site/drone clips
- Layer 2: procedural aerial world (VisDrone class GT)
- Layer 3: live ingest (browser 3D; optional / deferred for site fidelity)

All publish to Kafka `inference.frames`; detections return on
`inference.detections` and are scored against exercise ground truth.
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
from environments import list_demo_environments
from frames_l1 import (
    CorpusSource,
    VideoSource,
    list_videos,
    resolve_video,
)
from frames_l2 import SCENARIOS, SyntheticSource
from scoring import GTBox
from transport import KafkaTransport

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CORPUS_MANIFEST = os.getenv(
    "SIM_CORPUS_MANIFEST", "data/visdrone-yolo/corpus/corpus.json"
)
MAX_EXERCISES = 8
L3_CAP = 4096

# Agent-replay JSON name → preferred MP4 stem (under SIM_VIDEO_DIR), then L1 seq.
AGENT_REPLAY_VIDEO = {
    "railway_line_replay.json": "site-railway-0000001",
    "railway_line": "site-railway-0000001",
}


class ExerciseCreate(BaseModel):
    layer: int = Field(ge=1, le=3)
    scenario: str = "railway-yard"
    fps: float = Field(default=10.0, gt=0, le=30)
    sequence: Optional[str] = None
    video: Optional[str] = None  # MP4 id/filename for layer 1
    start_offset: int = 0
    frames: Optional[int] = Field(default=None, gt=0)
    # When set, prefer the mapped site video for that agent-replay scenario.
    agent_replay: Optional[str] = None


class IngestFrame(BaseModel):
    image_b64: str
    gt: list[dict] = Field(default_factory=list, description="[{class_name, bbox: [x0,y0,x1,y1] normalized}]")
    width: int = Field(default=640, gt=0)
    height: int = Field(default=360, gt=0)


manager: ExerciseManager


def _l1_corpus_provider(manifest: str, sequence: Optional[str], start_offset: int, frames: Optional[int]):
    src = CorpusSource(
        manifest_path=manifest,
        sequence=sequence,
        start_offset=start_offset,
        frames_limit=frames,
    )
    src.load()

    def provider(idx: int):
        return src.frame(idx)

    return provider, src.meta, src.sequence_ids, len(src)


def _l1_video_provider(video: str, start_offset: int, frames: Optional[int]):
    path = resolve_video(video)
    src = VideoSource(video_path=path, start_offset=start_offset, frames_limit=frames)
    src.load()
    if len(src) == 0:
        raise HTTPException(status_code=400, detail=f"video has no frames: {path.name}")

    def provider(idx: int):
        return src.frame(idx)

    return provider, src.meta, None, len(src)


def _l2_provider(scenario: str, fps: float):
    spec = SCENARIOS.get(scenario)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown layer2 scenario '{scenario}'; available: {sorted(SCENARIOS)}",
        )
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
    return {"status": "ok", "videos": len(list_videos()), "corpus": Path(CORPUS_MANIFEST).exists()}


@app.get("/layers")
def layers() -> dict:
    available = Path(CORPUS_MANIFEST).exists()
    seqs: list[str] = []
    if available:
        try:
            src = CorpusSource(manifest_path=CORPUS_MANIFEST)
            seqs = src.sequence_ids
        except Exception:
            seqs = []
    videos = list_videos()
    environments = list_demo_environments()
    return {
        "1": {
            "name": "real aerial footage (corpus + MP4)",
            "scenario": "visdrone-corpus",
            "available": available or bool(videos),
            "sequences": seqs,
            "videos": videos,
            "environments": environments,
            "frames_hint": 548,
        },
        "2": {"name": "procedural aerial compose", "scenarios": sorted(SCENARIOS)},
        "3": {
            "name": "live ingest (3D world — deferred for site fidelity)",
            "scenario": "browser-world",
            "note": "Prefer L1 MP4 / corpus for real human video. L3 stays available for FPV experiments.",
        },
    }


@app.get("/videos")
def videos() -> list[dict]:
    return list_videos()


@app.post("/exercises", status_code=201)
def create_exercise(body: ExerciseCreate) -> dict:
    if len(manager.exercises) >= MAX_EXERCISES:
        raise HTTPException(status_code=429, detail="too many exercises; stop one first")

    video = body.video
    if body.agent_replay and not video:
        mapped = AGENT_REPLAY_VIDEO.get(body.agent_replay) or AGENT_REPLAY_VIDEO.get(
            Path(body.agent_replay).name
        )
        if mapped:
            try:
                resolve_video(mapped)
                video = mapped
            except FileNotFoundError:
                video = None

    if body.layer == 1:
        if video:
            try:
                provider, meta, _, total = _l1_video_provider(
                    video, body.start_offset, body.frames
                )
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            frames_total = body.frames or total
            scenario = Path(video).stem if "/" not in video else Path(video).stem
            fps = body.fps
        else:
            if not Path(CORPUS_MANIFEST).exists():
                raise HTTPException(
                    status_code=400,
                    detail="corpus manifest not found; build it with backend/ml/build_corpus.py "
                    "or pass video= for MP4 L1",
                )
            provider, meta, _, total = _l1_corpus_provider(
                CORPUS_MANIFEST, body.sequence, body.start_offset, body.frames
            )
            frames_total = body.frames or total
            scenario = body.sequence or "visdrone-corpus"
            fps = body.fps
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
