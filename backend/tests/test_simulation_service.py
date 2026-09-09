"""Tests for the simulation lab backend (scoring, sources, engine).

Kept DB- and Kafka-free: the engine runs against a fake transport, and each
camera source is pure. Main-route creation logic is exercised by calling
`create_exercise` with a manager backed by the fake transport.
"""
import base64
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services" / "simulation_service"))

from scoring import Det, GTBox, aggregate, iou, match_frame  # noqa: E402
from frames_l2 import SCENARIOS, SyntheticSource  # noqa: E402
from transport import FakeTransport  # noqa: E402
from engine import ExerciseManager, FrameRec, _percentile, lesson_summary_overrides  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _gt(class_name="person", box=(0.1, 0.1, 0.5, 0.5)) -> GTBox:
    return GTBox(class_name=class_name, bbox=list(box))


def _det(class_name="person", box=(0.1, 0.1, 0.5, 0.5), conf=0.9, frame="ex:0", threat=None) -> Det:
    return Det(
        frame_id=frame,
        class_name=class_name,
        confidence=conf,
        bbox=list(box),
        threat_score=threat if threat is not None else conf,
    )


class TestScoring:
    def test_iou(self):
        assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0
        assert iou([0, 0, 1, 1], [1, 0, 2, 1]) == 0.0
        assert 0.6 < iou([0.1, 0.1, 0.6, 0.6], [0.15, 0.15, 0.65, 0.65]) < 0.7

    def test_match_tp_fp_fn(self):
        res = match_frame(
            [_gt(), _gt("car", [0.6, 0.6, 0.8, 0.8])],
            [_det(conf=0.9), _det("car", [0.1, 0.1, 0.5, 0.5], conf=0.8)],
        )
        assert len(res.tp) == 1
        assert len(res.fp) == 1
        assert len(res.fn) == 1

    def test_alert_candidates_threshold(self):
        res = match_frame(
            [_gt()],
            [_det(conf=0.95, threat=0.72), _det("car", [0.9, 0.9, 1.0, 1.0], conf=0.5, threat=0.5)],
        )
        assert res.alert_candidates == 1

    def test_aggregate_derives_gt_per_class(self):
        r1 = match_frame([_gt()], [_det(conf=0.9)])
        r2 = match_frame([_gt("car", [0.6, 0.6, 0.8, 0.8])], [_det("car", [0.1, 0.1, 0.5, 0.5])])
        overall, per_class = aggregate([r1, r2])
        assert overall.tp == 1 and overall.fp == 1 and overall.fn == 1 and overall.gt == 2
        assert per_class["person"].tp == 1
        assert per_class["car"].fn == 1

    def test_lesson_summary_flags_fp_bias(self):
        many_fp = [match_frame([_gt()], [_det(conf=0.9), _det(conf=0.85), _det(conf=0.8)]) for _ in range(6)]
        overall, per_class = aggregate(many_fp)
        lessons = lesson_summary_overrides(overall, per_class)
        assert any(l["kind"] == "false_positive_bias" for l in lessons)
        assert any(l["kind"] == "alert_pressure" for l in lessons)


class TestL2Synthetic:
    def test_frame_shape_and_gt(self):
        src = SyntheticSource(spec=SCENARIOS["railway-yard"], fps=10.0)
        b64, w, h, gts = src.frame(0)
        raw = base64.b64decode(b64)
        assert raw[:2] == b"\xff\xd8"  # JPEG magic
        assert w == 960 and h == 540
        assert gts, "expected scripted entities to produce ground truth"

    def test_deterministic(self):
        a = SyntheticSource(spec=SCENARIOS["market-square"], fps=10.0)
        b = SyntheticSource(spec=SCENARIOS["market-square"], fps=10.0)
        assert a.frame(5)[0] == b.frame(5)[0]

    def test_boxes_inside_frame(self):
        src = SyntheticSource(spec=SCENARIOS["railway-yard"], fps=10.0)
        for idx in (0, 3, 9):
            _, _, _, gts = src.frame(idx)
            for gt in gts:
                x0, y0, x1, y1 = gt.bbox
                assert 0.0 <= x0 < x1 <= 1.0
                assert 0.0 <= y0 < y1 <= 1.0
                assert gt.class_name in ("person", "vehicle")


class TestEngine:
    def _manager(self) -> tuple[ExerciseManager, FakeTransport]:
        transport = FakeTransport()
        return ExerciseManager(transport), transport

    def test_pump_and_live_score(self):
        mgr, transport = self._manager()
        src = SyntheticSource(spec=SCENARIOS["railway-yard"], fps=60.0)
        ex = mgr.start(layer=2, scenario="railway-yard", fps=60.0, provider=src.frame, frames_total=5, meta=src.meta)
        ex.state = "running"
        import time
        while ex.frame_index < 5 and time.time() - ex.started_ms < 5:
            time.sleep(1 / 60.0)
        assert ex.state == "completed"
        assert ex.frames_total == 5
        assert ex.frame_index == 5
        assert transport.sent, "frames must be published to the real pipeline topic"

    def test_detection_scored_and_tallied(self):
        mgr, transport = self._manager()
        src = SyntheticSource(spec=SCENARIOS["railway-yard"], fps=120.0)
        ex = mgr.start(layer=2, scenario="railway-yard", fps=120.0, provider=src.frame, frames_total=3, meta=src.meta)
        ex.record_frame(f"{ex.id}:0", [_gt()], FrameRec(f"{ex.id}:0", "", 640, 360, 0))
        claimed = ex.record_detections(
            f"{ex.id}:0", {"detections": [{"class_name": "person", "confidence": 0.9, "bbox": [0.1, 0.1, 0.47, 0.47], "model_version": "v", "metadata": {}}], "frame_id": f"{ex.id}:0"}
        )
        assert claimed is True
        assert ex.tally.tp == 1
        status = ex.status()
        assert status["scoreboard"]["overall"]["recall"] == 1.0
        assert status["alerts"]["candidates"] == 1

    def test_l3_ingest_iife_claiming(self):
        mgr, transport = ExerciseManager(FakeTransport()), None
        ex = mgr.start(layer=3, scenario="l3-ingest", fps=10.0, provider=(lambda idx: ("", 0, 0, [])), frames_total=None, meta=(lambda idx: {}))
        ex.record_frame("ex3:0", [_gt()], FrameRec("ex3:0", "", 640, 360, 0))
        assert ex.state == "running"
        mgr.stop(ex.id)
        assert ex.state == "stopped"


class TestMainRoutes:
    def test_create_layer2_exercise(self):
        import main as sim_main
        transport = FakeTransport()
        mgr = ExerciseManager(transport)
        sim_main.manager = mgr
        from main import ExerciseCreate
        resp = sim_main.create_exercise(ExerciseCreate(layer=2, scenario="railway-yard", fps=10.0, frames=5))
        assert resp["layer"] == 2
        assert resp["frames_total"] == 5
        assert resp["state"] in ("running", "pending")

    def test_unknown_layer2_scenario_404(self):
        import main as sim_main
        sim_main.manager = ExerciseManager(FakeTransport())
        from main import ExerciseCreate, HTTPException
        try:
            sim_main.create_exercise(ExerciseCreate(layer=2, scenario="does-not-exist"))
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 404

    def test_percentile(self):
        assert _percentile([], 0.5) is None
        assert _percentile([10, 20, 30], 0.5) == 20