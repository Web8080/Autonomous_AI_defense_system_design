"""Detection-vs-GT scoring for simulation exercises.

Every simulation layer feeds camera frames through the *real* product path
(inference.frames -> inference service -> inference.detections -> detections
persistence -> alert service). What the production stack actually produced is
scored here against the exercise ground truth so the lab measures the product,
not a parallel toy.

Session-scope counts and lesson hooks ("what would we change in the product?")
also live here so the dashboard can surface lessons-back for the hardware path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

IOU_THRESHOLD = 0.5
ALERT_THRESHOLD = 0.7  # mirrors alert_service threat-score gate


@dataclass
class GTBox:
    """Ground-truth box, normalized xyxy."""

    class_name: str
    bbox: list[float]


@dataclass
class Det:
    """Detection reported by the production inference service."""

    frame_id: str
    class_name: str
    confidence: float
    bbox: list[float]
    threat_score: float = 0.0
    model_version: str = ""
    matched: bool = False


@dataclass
class FrameResult:
    """Outcome of one camera frame through the real pipeline."""

    frame_id: str
    tp: list[Det] = field(default_factory=list)
    fp: list[Det] = field(default_factory=list)
    fn: list[GTBox] = field(default_factory=list)
    alert_candidates: int = 0


def iou(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    a_area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    b_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = a_area + b_area - inter
    return inter / union if union > 0 else 0.0


def match_frame(gts: list[GTBox], dets: list[Det]) -> FrameResult:
    """Greedy same-class IoU match (sorted by confidence desc)."""
    result = FrameResult(frame_id=dets[0].frame_id if dets else "")
    used: set[int] = set()
    for det in sorted(dets, key=lambda d: d.confidence, reverse=True):
        best_idx: Optional[int] = None
        best_score = IOU_THRESHOLD
        for i, gt in enumerate(gts):
            if i in used or gt.class_name != det.class_name:
                continue
            score = iou(det.bbox, gt.bbox)
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is not None:
            used.add(best_idx)
            det.matched = True
            result.tp.append(det)
        else:
            result.fp.append(det)
        if (det.threat_score or det.confidence) >= ALERT_THRESHOLD:
            result.alert_candidates += 1
    result.fn = [gt for i, gt in enumerate(gts) if i not in used]
    return result


@dataclass
class ClassCounts:
    gt: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    alert_candidates: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.gt
        return self.tp / denom if denom else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def aggregate(frame_results: list[FrameResult]) -> tuple[ClassCounts, dict[str, ClassCounts]]:
    """Overall + per-class counts across every scored frame.

    Per-class GT is derived from the 1:1 greedy match: every ground-truth box is
    either matched (one true positive) or unmatched (one false negative), so a
    class's GT = its matched TP + its FN.
    """
    overall = ClassCounts()
    per_class: dict[str, ClassCounts] = {}
    for fr in frame_results:
        overall.alert_candidates += fr.alert_candidates
        for det in fr.tp:
            overall.tp += 1
            per_class.setdefault(det.class_name, ClassCounts()).tp += 1
        for det in fr.fp:
            overall.fp += 1
            per_class.setdefault(det.class_name, ClassCounts()).fp += 1
        for gt in fr.fn:
            overall.fn += 1
            per_class.setdefault(gt.class_name, ClassCounts()).fn += 1
    overall.gt = overall.tp + overall.fn
    for cc in per_class.values():
        cc.gt = cc.tp + cc.fn
    return overall, per_class


