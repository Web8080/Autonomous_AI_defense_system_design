"""Exercise engine: pump frames -> production pipeline -> score what came back.

Each exercise owns a camera source (L1 corpus / L2 synthetic / L3 live ingest),
pumps frames into the real `inference.frames` topic, and matches the real
`inference.detections` output back against the exercise ground truth live.
```
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from scoring import (
    Det,
    GTBox,
    FrameResult,
    ClassCounts,
    aggregate,
    match_frame,
)
from transport import Transport

ALERT_DISPATCH = "ALERT_DISPATCH"
UNREGISTERED_SKIP = "skip"


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class FrameRec:
    frame_id: str
    image_b64: str
    width: int
    height: int
    index: int


@dataclass
class ResultRec:
    frame_id: str
    tp: int
    fp: int
    fn: int
    gt: int
    alerts: int
    latency_ms: Optional[int]
    detections: list[dict] = field(default_factory=list)
    gts: list[dict] = field(default_factory=list)


class Exercise:
    def __init__(self, layer: int, scenario: str, fps: float, frames_total: Optional[int]) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.layer = layer
        self.scenario = scenario
        self.fps = fps
        self.frames_total = frames_total
        self.state = "pending"
        self.frame_index = 0
        self.last_error: Optional[str] = None
        self.created_ms = _now_ms()
        self.started_ms: Optional[int] = None

        self._lock = threading.RLock()
        self.frames_gt: dict[str, list[GTBox]] = {}
        self._sent_ms: dict[str, int] = {}
        self.latencies_ms: deque[int] = deque(maxlen=2000)
        self.results: deque[ResultRec] = deque(maxlen=120)
        self.recent_frames: deque[FrameRec] = deque(maxlen=40)
        self.tally = ClassCounts()
        self.per_class: dict[str, ClassCounts] = {}
        self.frame_origins: list[dict] = []

    def record_frame(self, frame_id: str, gts: list[GTBox], rec: FrameRec) -> None:
        with self._lock:
            self.frames_gt[frame_id] = gts
            self._sent_ms[frame_id] = _now_ms()
            self.recent_frames.append(rec)
            self.frame_index += 1

    def record_detections(self, frame_id: str, msg: dict) -> bool:
        """Score the production output for one frame. Returns True if claimed."""
        with self._lock:
            dets_raw = msg.get("detections") or []
            if not dets_raw:
                return False
            frame_colon = frame_id.find(":")
            if frame_colon == -1 or frame_id[:frame_colon] != self.id:
                return False
            gts = self.frames_gt.pop(frame_id, None)
            sent = self._sent_ms.pop(frame_id, None)
            if gts is None:
                return False
            dets: list[Det] = []
            for d in dets_raw:
                meta = d.get("metadata") or {}
                if meta.get("provenance") == "stub":
                    continue
                bbox = d.get("bbox")
                if not bbox or len(bbox) != 4:
                    continue
                conf = float(d.get("confidence") or 0.0)
                dets.append(
                    Det(
                        frame_id=frame_id,
                        class_name=d.get("class_name", "unknown"),
                        confidence=conf,
                        bbox=[float(v) for v in bbox],
                        threat_score=float(d.get("threat_score") or conf),
                        model_version=d.get("model_version", ""),
                    )
                )
            res = match_frame(gts, dets)
            latency = _now_ms() - sent if sent is not None else None
            if latency is not None:
                self.latencies_ms.append(latency)
            overall, per_class = aggregate([res])
            self.tally.tp += overall.tp
            self.tally.fp += overall.fp
            self.tally.fn += overall.fn
            self.tally.gt += overall.gt
            self.tally.alert_candidates += overall.alert_candidates
            for name, cc in per_class.items():
                tgt = self.per_class.setdefault(name, ClassCounts())
                tgt.tp += cc.tp
                tgt.fp += cc.fp
                tgt.fn += cc.fn
                tgt.gt += cc.gt
                tgt.alert_candidates += cc.alert_candidates
            self.results.append(
                ResultRec(
                    frame_id=frame_id,
                    tp=len(res.tp),
                    fp=len(res.fp),
                    fn=len(res.fn),
                    gt=len(gts),
                    alerts=res.alert_candidates,
                    latency_ms=latency,
                    detections=[
                        {
                            "class_name": d.class_name,
                            "confidence": d.confidence,
                            "bbox": d.bbox,
                            "threat_score": d.threat_score,
                            "model_version": d.model_version,
                            "matched": d.matched,
                        }
                        for d in (res.tp + res.fp)
                    ],
                    gts=[
                        {"class_name": g.class_name, "bbox": g.bbox} for g in gts
                    ],
                )
            )
            return True


    def status(self, trending: dict | None = None) -> dict:
        with self._lock:
            overall, per_class = aggregate_historical(self)
            p50 = _percentile(list(self.latencies_ms), 0.50)
            p95 = _percentile(list(self.latencies_ms), 0.95)
            recent = [
                {
                    "frame_id": r.frame_id,
                    "tp": r.tp,
                    "fp": r.fp,
                    "fn": r.fn,
                    "gt": r.gt,
                    "alerts": r.alerts,
                    "latency_ms": r.latency_ms,
                    "detections": r.detections,
                    "gts": r.gts,
                }
                for r in list(self.results)[::-1][:30]
            ]
            return {
                "id": self.id,
                "layer": self.layer,
                "scenario": self.scenario,
                "state": self.state,
                "fps": self.fps,
                "frame_index": self.frame_index,
                "frames_total": self.frames_total,
                "last_error": self.last_error,
                "scoreboard": {
                    "overall": _counts_dict(overall),
                    "per_class": {k: _counts_dict(v) for k, v in per_class.items()},
                },
                "alerts": {"candidates": self.tally.alert_candidates, "threshold": 0.7},
                "latency_ms": {"p50": p50, "p95": p95, "count": len(self.latencies_ms)},
                "recent_frames": [
                    {"frame_id": f.frame_id, "image_b64": f.image_b64, "width": f.width, "height": f.height, "index": f.index}
                    for f in list(self.recent_frames)[::-1][:16]
                ],
                "recent_results": recent,
                "metrics": {
                    "frames_sent": self.frame_index,
                    "frames_scored": len(self.latencies_ms),
                    "scoring_drain": round((len(self.latencies_ms) / self.frame_index) * 100, 1) if self.frame_index else 0.0,
                },
                "lessons": lesson_summary_overrides(self.tally, self.per_class),
                **({"trending": trending} if trending else {}),
            }


def aggregate_historical(ex: Exercise) -> tuple[ClassCounts, dict[str, ClassCounts]]:
    return ex.tally, ex.per_class


def lesson_summary_overrides(overall: ClassCounts, per_class: dict[str, ClassCounts]) -> list[dict]:
    lessons: list[dict] = []
    fp_sorted = sorted(per_class.items(), key=lambda kv: kv[1].fp, reverse=True)
    for name, cc in fp_sorted[:3]:
        denom = cc.tp + cc.fp
        if cc.fp > 0 and denom >= 5:
            lessons.append(
                {
                    "kind": "false_positive_bias",
                    "class_name": name,
                    "tp": cc.tp,
                    "fp": cc.fp,
                    "fp_share": round(cc.fp / denom, 3),
                    "hint": f"tighten threshold / zone-mask for '{name}'",
                }
            )
    for name, cc in sorted(per_class.items(), key=lambda kv: kv[1].recall):
        if cc.gt >= 5 and cc.recall < 0.5:
            lessons.append(
                {
                    "kind": "recall_gap",
                    "class_name": name,
                    "gt": cc.gt,
                    "tp": cc.tp,
                    "recall": round(cc.recall, 3),
                    "hint": f"augment/retrain for '{name}'",
                }
            )
    if overall.alert_candidates and overall.gt:
        lessons.append(
            {
                "kind": "alert_pressure",
                "alert_candidates": overall.alert_candidates,
                "gt": overall.gt,
                "rate": round(overall.alert_candidates / max(1, overall.gt), 3),
                "hint": "alert service pressure during on-site validation",
            }
        )
    return lessons


def _counts_dict(cc: ClassCounts) -> dict:
    return {
        "gt": cc.gt,
        "tp": cc.tp,
        "fp": cc.fp,
        "fn": cc.fn,
        "precision": round(cc.precision, 3),
        "recall": round(cc.recall, 3),
        "f1": round(cc.f1, 3),
        "alert_candidates": cc.alert_candidates,
    }


def _percentile(values: list[int], q: float) -> Optional[int]:
    if not values:
        return None
    vals = sorted(values)
    k = int((len(vals) - 1) * q)
    return vals[k]


Provider = Callable[[int], tuple[str, int, int, list[GTBox]]]
MetaProvider = Callable[[int], dict]


class ExerciseManager:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.exercises: dict[str, Exercise] = {}
        self._lock = threading.RLock()
        self.transport.start(self._dispatch)

    def _dispatch(self, msg: dict) -> None:
        frame_id = str(msg.get("frame_id") or "")
        if ":" not in frame_id:
            return
        ex_id = frame_id.split(":", 1)[0]
        with self._lock:
            ex = self.exercises.get(ex_id)
        if ex and ex.state == "running":
            try:
                ex.record_detections(frame_id, msg)
            except Exception:  # noqa: BLE001
                pass

    def start(
        self,
        layer: int,
        scenario: str,
        fps: float,
        provider: Provider,
        frames_total: Optional[int],
        meta: MetaProvider,
    ) -> Exercise:
        ex = Exercise(layer=layer, scenario=scenario, fps=fps, frames_total=frames_total)
        with self._lock:
            self.exercises[ex.id] = ex
        ex.state = "running"
        ex.started_ms = _now_ms()
        # Layer 3 is live-ingest only: frames come from the dashboard's 3D world
        # via POST /exercises/{id}/ingest. No pump thread – avoids publishing empty frames.
        if layer == 3:
            return ex
        thread = threading.Thread(target=self._pump, args=(ex, provider, meta), daemon=True)
        thread.start()
        return ex

    def _pump(self, ex: Exercise, provider: Provider, meta: MetaProvider) -> None:
        try:
            idx = 0
            while ex.state == "running":
                if ex.frames_total is not None and idx >= ex.frames_total:
                    break
                b64, w, h, gts = provider(idx)
                frame_id = f"{ex.id}:{idx}"
                msg = {
                    "asset_id": f"sim-{ex.layer}",
                    "frame_id": frame_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "image_b64": b64,
                    "source": f"simulation/l{ex.layer}",
                    "camera": scenario_name(ex.scenario),
                }
                ex.record_frame(
                    frame_id, gts, FrameRec(frame_id=frame_id, image_b64=b64, width=w, height=h, index=idx)
                )
                try:
                    self.transport.send_frame(msg)
                except RuntimeError as exc:
                    ex.last_error = str(exc)
                    break
                if len(ex.frame_origins) < 8192:
                    ex.frame_origins.append({"index": idx, "meta": meta(idx)})
                idx += 1
                if ex.frames_total is None or idx < ex.frames_total:
                    time.sleep(1.0 / max(0.5, ex.fps))
        finally:
            if ex.frames_total is not None:
                ex.state = "completed"
            else:
                ex.state = "stopped"

    def stop(self, ex_id: str) -> bool:
        with self._lock:
            ex = self.exercises.get(ex_id)
        if not ex:
            return False
        ex.state = "stopped"
        return True


def scenario_name(scenario: str) -> str:
    return scenario.replace("-", " ").title()