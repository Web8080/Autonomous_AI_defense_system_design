"""Tests for the model promotion gate: the deterministic rule that decides what
may fly. The gate fails closed; the tests below pin that down."""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "services" / "ml_service"))

from eval_gate import (  # noqa: E402
    EvalRecord,
    GateConfig,
    GateDecision,
    evaluate_gate,
    check_regression,
)


def passing_record(**overrides):
    base = dict(
        benchmark="visdrone-detect-frozen-2026-09",
        benchmark_hash="a" * 64,
        map_50=0.55,
        recall_at_conf=0.80,
        recall_at_target_far=0.90,
        false_alarms_per_hour=0.5,
        latency_p95_ms=45.0,
        per_class={"person": {"recall": 0.80}, "car": {"recall": 0.85}},
        class_names=["person", "car"],
    )
    base.update(overrides)
    return EvalRecord.from_mapping(base, base["class_names"])


def test_passing_record_passes():
    assert evaluate_gate(passing_record()).passed


def test_benchmark_mismatch_fails():
    r = passing_record(benchmark="some-other-benchmark")
    assert not evaluate_gate(r).passed


def test_missing_benchmark_hash_fails():
    r = passing_record(benchmark_hash=None)
    assert not evaluate_gate(r).passed


def test_null_metric_fails_closed():
    # Every product metric NULL must be a rejection, not a pass.
    for field in ("map_50", "recall_at_conf", "recall_at_target_far",
                  "false_alarms_per_hour", "latency_p95_ms"):
        assert not evaluate_gate(passing_record(**{field: None})).passed, field


def test_nan_fails_closed():
    import math
    assert not evaluate_gate(passing_record(map_50=math.nan)).passed
    assert not evaluate_gate(passing_record(recall_at_target_far=float("inf"))).passed


def test_false_alarms_per_hour_greater_than_target_fails():
    assert not evaluate_gate(passing_record(false_alarms_per_hour=3.0)).passed


def test_zero_false_alarms_is_not_credible():
    # Zero FAR on a real corpus means the corpus is broken; must fail closed.
    assert not evaluate_gate(passing_record(false_alarms_per_hour=0.0)).passed


def test_recall_at_target_far_too_low_fails():
    assert not evaluate_gate(passing_record(recall_at_target_far=0.5)).passed


def test_latency_over_budget_fails():
    assert not evaluate_gate(passing_record(latency_p95_ms=200.0)).passed


def test_missing_per_class_rejects_each_claimed_class():
    # Claimed "person" but no per-class entry -> reject.
    r = passing_record(per_class={"car": {"recall": 0.9}})
    assert not evaluate_gate(r).passed


def test_weak_per_class_rejects():
    r = passing_record(per_class={"person": {"recall": 0.3}, "car": {"recall": 0.9}})
    assert not evaluate_gate(r).passed


def test_extra_per_class_unclaimed_is_ignored():
    r = passing_record(per_class={"person": {"recall": 0.8}, "car": {"recall": 0.85},
                                  "van": {"recall": 0.1}})
    assert evaluate_gate(r).passed


def test_failures_are_explained_in_notes():
    d = evaluate_gate(passing_record(map_50=0.2, false_alarms_per_hour=9.0))
    notes = d.detail.lower()
    assert "map_50" in notes
    assert "false_alarms_per_hour" in notes
    assert isinstance(d, GateDecision)


def test_regression_never_worse_than_incumbent():
    incumbent = passing_record(recall_at_target_far=0.90, false_alarms_per_hour=0.5)
    assert check_regression(incumbent, incumbent) == []
    worse_recall = passing_record(recall_at_target_far=0.80, false_alarms_per_hour=0.5)
    assert check_regression(worse_recall, incumbent)
    worse_far = passing_record(recall_at_target_far=0.90, false_alarms_per_hour=1.0)
    assert check_regression(worse_far, incumbent)


def test_regression_better_or_equal_passes():
    incumbent = passing_record(recall_at_target_far=0.87, false_alarms_per_hour=0.9)
    better = passing_record(recall_at_target_far=0.93, false_alarms_per_hour=0.3)
    assert check_regression(better, incumbent) == []


def test_regression_no_incumbent_is_no_obstacle():
    assert check_regression(passing_record(), None) == []


def test_gate_config_is_frozen():
    cfg = GateConfig()
    d = evaluate_gate(passing_record(), cfg)
    assert d.passed