"""Tests for the model registry service (schema/validation/gate wiring).

DB-backed paths (promote with real rows, incumbent archiving) are verified
end-to-end; unit tests cover what is pure: health, validation, and the
assess() function that the /evals endpoint runs server-side.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

import sys
import os
from pathlib import Path
from conftest import load_service_app

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# load via conftest pattern for import isolation
app = load_service_app("ml_service")


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["service"] == "ml-service"


def test_model_create_validates_task_and_precision():
    from services.ml_service.main import ModelCreate
    with pytest.raises(ValidationError):
        ModelCreate(name="m", version="1", task="mind-reading")
    with pytest.raises(ValidationError):
        ModelCreate(name="m", version="1", precision="fp128")
    ok = ModelCreate(name="yolov8s", version="1.2", class_names=["person"])
    assert ok.runtime == "pytorch"


def test_eval_create_validates_ranges():
    from services.ml_service.main import EvalCreate
    with pytest.raises(ValidationError):
        EvalCreate(benchmark="b", benchmark_hash="h", map_50=1.5)
    with pytest.raises(ValidationError):
        EvalCreate(benchmark="b", benchmark_hash="h", false_alarms_per_hour=-1)


def _body(**over):
    base = dict(
        benchmark="visdrone-detect-frozen-2026-09",
        benchmark_hash="f" * 64,
        map_50=0.61,
        recall_at_conf=0.82,
        recall_at_target_far=0.91,
        false_alarms_per_hour=0.4,
        latency_p95_ms=38.0,
        per_class={"person": {"recall": 0.81}, "car": {"recall": 0.86}},
    )
    base.update(over)
    return base


def test_assess_passing_eval():
    from services.ml_service.main import assess
    out = assess(_body(), class_names=["person", "car"])
    assert out["passed"] is True
    assert out["absolute_passed"] is True
    assert out["regressions"] == []


def test_assess_fails_on_weak_product_metrics():
    from services.ml_service.main import assess
    out = assess(_body(false_alarms_per_hour=5.0, recall_at_target_far=0.6),
                 class_names=["person", "car"])
    assert out["passed"] is False
    assert "false_alarms_per_hour" in out["gate_notes"]
    assert "recall_at_target_far" in out["gate_notes"]


def test_assess_applies_incumbent_regression():
    from services.ml_service.main import assess
    incumbent = {"recall_at_target_far": 0.91, "false_alarms_per_hour": 0.4,
                 "class_names": ["person", "car"]}
    worse = assess(_body(recall_at_target_far=0.7), class_names=["person", "car"],
                   incumbent=incumbent)
    assert worse["passed"] is False
    assert any("regression" in n for n in worse["gate_notes"].split(";"))


def test_assess_injection_cannot_force_pass():
    """A client claiming passed_gate must not matter: the server recomputes."""
    from services.ml_service.main import assess
    bad = _body(map_50=0.1, recall_at_conf=0.4, recall_at_target_far=0.2,
                false_alarms_per_hour=9.0, latency_p95_ms=300)
    out = assess(bad, class_names=["person", "car"])
    assert out["passed"] is False
    assert out["absolute_passed"] is False