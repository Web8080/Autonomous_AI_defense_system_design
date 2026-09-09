"""
Evaluation gate: the deterministic rule that decides whether a model may be
promoted to production.

This is the *gate*, not the benchmark runner. The runner produces an eval record
against the frozen benchmark; this module decides, in code, whether that record
satisfies the product's promotion criteria. It is deliberately pure: no I/O, no
database, all inputs passed in, all failure modes tested. Anything this module
rejects cannot reach production regardless of what any other process does.

The product metric is not mAP. The metric that decides whether a site keeps the
system switched on is false alarms per flight hour at target recall. mAP is
supporting context; recall_at_target_far + false_alarms_per_hour are the gate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateConfig:
    """Promotion thresholds. Frozen so the gate is the same every run.

    Calibrated against VisDrone literature baselines (AISKYEYE, DET task):
    YOLOv8n ≈ 0.28–0.32 and YOLOv8s ≈ 0.37–0.44 mAP@50 with full-scale training
    and COCO-pretrained transfer. A threshold above what the model class can
    reach would silently prevent any legitimate certification; the product
    metrics below are what actually keep a site switched on, and they stay
    strict.
    """

    # The eval must have been run against exactly this benchmark identity.
    required_benchmark: str = "visdrone-detect-frozen-2026-09"
    min_map_50: float = 0.30
    # The two product metrics. A perimeter system with too many false alarms
    # gets unplugged regardless of benchmark score.
    max_false_alarms_per_hour: float = 2.0
    min_recall_at_target_far: float = 0.80
    min_recall_at_conf: float = 0.70
    max_latency_p95_ms: float = 150.0
    # Minimum per-class recall for every class the model claims to detect.
    # A model that detects "car" brilliantly and "person" terribly does not fly.
    # The floor is frequency-tiered because VisDrone is brutally imbalanced
    # (val: car ≈ 14k ground truths, bus ≈ 251): a uniform high floor would make
    # rare classes literally uncertifiable. Common classes demand 0.60; rare
    # classes (fewer than `rare_class_gt_threshold` val ground truths, proven by
    # `gt_count` IN THE EVAL, not claimed by the model) get 0.40. An eval that
    # does not record `gt_count` is judged against the strict floor - fail toward
    # strict, never toward permissive.
    min_per_class_recall: float = 0.60
    min_per_class_recall_rare: float = 0.40
    rare_class_gt_threshold: int = 1500


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def detail(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "no obstacles"


@dataclass(frozen=True)
class EvalRecord:
    """Shape of `ml.model_evals` as the gate reads it. Missing/NULL -> fail closed."""

    benchmark: str | None = None
    benchmark_hash: str | None = None
    map_50: float | None = None
    false_alarms_per_hour: float | None = None
    recall_at_target_far: float | None = None
    recall_at_conf: float | None = None
    latency_p95_ms: float | None = None
    per_class: dict | None = None
    class_names: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, m: dict, class_names: list[str] | None = None) -> "EvalRecord":
        def _f(v):
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        return cls(
            benchmark=(m.get("benchmark") or None),
            benchmark_hash=(m.get("benchmark_hash") or None),
            map_50=_f(m.get("map_50")),
            false_alarms_per_hour=_f(m.get("false_alarms_per_hour")),
            recall_at_target_far=_f(m.get("recall_at_target_far")),
            recall_at_conf=_f(m.get("recall_at_conf")),
            latency_p95_ms=_f(m.get("latency_p95_ms")),
            per_class=m.get("per_class") or {},
            class_names=class_names if class_names is not None else (m.get("class_names") or []),
        )


def _bounded(v: float | None) -> bool:
    return v is not None and not math.isnan(v) and not math.isinf(v)


def evaluate_gate(record: EvalRecord, config: GateConfig | None = None) -> GateDecision:
    """Evaluate one eval record against the gate. Fail closed on anything unusable."""
    cfg = config or GateConfig()
    reasons: list[str] = []

    # --- Identity: the eval must be against the frozen, quarantined benchmark ----
    if record.benchmark != cfg.required_benchmark:
        reasons.append(
            f"benchmark mismatch: {record.benchmark!r}, required {cfg.required_benchmark!r}"
        )
    if not record.benchmark_hash:
        reasons.append("missing benchmark_hash: cannot prove which data was evaluated")

    # --- Aggregate quality --------------------------------------------------------
    if not _bounded(record.map_50):
        reasons.append("missing/invalid map_50")
    elif record.map_50 < cfg.min_map_50:
        reasons.append(f"map_50 {record.map_50:.3f} < {cfg.min_map_50}")

    if not _bounded(record.recall_at_conf):
        reasons.append("missing/invalid recall_at_conf")
    elif record.recall_at_conf < cfg.min_recall_at_conf:
        reasons.append(f"recall_at_conf {record.recall_at_conf:.3f} < {cfg.min_recall_at_conf}")

    # --- Product metrics: false alarms per hour at target recall ------------------
    if not _bounded(record.recall_at_target_far):
        reasons.append("missing/invalid recall_at_target_far")
    elif record.recall_at_target_far < cfg.min_recall_at_target_far:
        reasons.append(
            f"recall_at_target_far {record.recall_at_target_far:.3f} < {cfg.min_recall_at_target_far}"
        )

    if not _bounded(record.false_alarms_per_hour):
        reasons.append("missing/invalid false_alarms_per_hour")
    elif record.false_alarms_per_hour > cfg.max_false_alarms_per_hour:
        reasons.append(
            f"false_alarms_per_hour {record.false_alarms_per_hour:.3f} > {cfg.max_false_alarms_per_hour}"
        )
    elif record.false_alarms_per_hour <= 0.0:
        # A zero FAR on any real corpus is a measurement bug, not a good score.
        reasons.append(
            f"false_alarms_per_hour {record.false_alarms_per_hour:.3f} is not credible "
            "(zero false alarms on a real corpus means the corpus is empty or broken)"
        )

    # --- Latency: must be measured on the target device ---------------------------
    if not _bounded(record.latency_p95_ms):
        reasons.append("missing/invalid latency_p95_ms")
    elif record.latency_p95_ms > cfg.max_latency_p95_ms:
        reasons.append(f"latency_p95 {record.latency_p95_ms:.2f}ms > {cfg.max_latency_p95_ms}ms")

    # --- Per-class: every claimed class must have an eval, above the floor ---------
    claimed = sorted({str(c) for c in record.class_names if str(c)})
    per_class = record.per_class or {}
    if claimed and not per_class:
        reasons.append("per_class breakdown missing although class_names are declared")
    for cls in claimed:
        rc = per_class.get(cls)
        gt_n = None
        if isinstance(rc, dict) and rc:
            gt_n = rc.get("gt_count")
            rc = rc.get("recall")
        if not _bounded(rc):
            reasons.append(f"per_class recall missing/invalid for {cls!r}")
            continue
        floor = cfg.min_per_class_recall
        if gt_n is not None:
            try:
                if int(gt_n) < cfg.rare_class_gt_threshold:
                    floor = cfg.min_per_class_recall_rare
            except (TypeError, ValueError):
                pass  # malformed gt_count -> strict floor
        if float(rc) < floor:
            reasons.append(
                f"per_class recall {cls!r} {float(rc):.3f} < {floor:.2f}"
            )

    return GateDecision(passed=not reasons, reasons=reasons)


def check_regression(new: EvalRecord, incumbent: EvalRecord | None) -> list[str]:
    """Product-metric regression check against the incumbent production model.

    A new model may not be worse than what currently flies on either product
    metric. This is deliberately conservative and easy to explain: promotion is
    never a step backwards on the numbers that keep the site switched on.
    """
    if incumbent is None:
        return []
    reasons: list[str] = []
    if _bounded(new.recall_at_target_far) and _bounded(incumbent.recall_at_target_far):
        if new.recall_at_target_far < incumbent.recall_at_target_far - 1e-9:
            reasons.append(
                f"regression: recall_at_target_far {new.recall_at_target_far:.3f} "
                f"< incumbent {incumbent.recall_at_target_far:.3f}"
            )
    if _bounded(new.false_alarms_per_hour) and _bounded(incumbent.false_alarms_per_hour):
        if new.false_alarms_per_hour > incumbent.false_alarms_per_hour + 1e-9:
            reasons.append(
                f"regression: false_alarms_per_hour {new.false_alarms_per_hour:.3f} "
                f"> incumbent {incumbent.false_alarms_per_hour:.3f}"
            )
    return reasons