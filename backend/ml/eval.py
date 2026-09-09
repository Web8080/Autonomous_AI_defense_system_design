"""Frozen-benchmark evaluation runner for the promotion gate.

Computes the ml.model_evals fields the gate checks, most importantly the
*pair* of product metrics:

- recall_at_target_far: the recall at the confidence threshold where the
  measured false-alarm rate does not exceed the target (swept over a temporal
  corpus).
- false_alarms_per_hour: total temporal false alarms / flight hours.

The static benchmark (VisDrone frozen split) gives map_50, per-class recall and
latency. FAPFH and recall-at-target-FAR CANNOT be measured on a static image
set — they require continuous flight segments with timestamps. This runner
therefore *requires* `--corpus-manifest` to emit those two fields. Without it,
the emitted eval records those fields as null and the promotion gate fails
closed. That is a feature: an eval that did not measure the product metric must
not certify a promotion, no matter how pretty the mAP.

Output: a JSON file shaped like ml.model_evals, ready to POST to
/ml/models/{id}/evals.

Usage:
    python -m ml.eval \
        --manifest manifests/frozen-visdrone.json \
        --weights artifacts/visdrone-yolov8s.pt \
        --corpus-manifest data/flight-corpus/corpus.json \
        --device cpu --latency-iters 100 \
        --out eval-v1-42.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "services"))
sys.path.insert(0, str(BACKEND / "services" / "ml_service"))

TARGET_FAR_PER_HOUR = 2.0


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0, ix1, iy1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def build_temporal_corpus(corpus_manifest: dict):
    """Yield (image_path, fps, gt_boxes) per segment for FAPFH + recall sweep."""
    samples = []
    for seg in corpus_manifest["segments"]:
        fps = float(seg["fps"])
        for frame in seg["frames"]:
            gt = [f["bbox"] for f in frame.get("annotations", []) if not f.get("ignore")]
            samples.append((frame["image"], fps, gt))
    return samples


def sweep_thresholds(model, samples, device):
    """Run detection per frame once; return recall/FAR inputs across thresholds."""
    frame_preds = []
    for image, fps, gt in samples:
        res = model(str(image), verbose=False, device=device)[0]
        preds = []
        if res.boxes is not None:
            for xyxy, conf, cls in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist(), res.boxes.cls.tolist()):
                preds.append({"bbox": xyxy, "conf": conf, "cls": int(cls)})
        frame_preds.append({"gt": gt, "preds": preds, "hours": 1.0 / fps})
    return frame_preds


def compute_product_metrics(frame_preds, target_far: float) -> dict:
    """Sweep confidence and find recall at the threshold where FAR <= target.

    FAR is measured per hour: a false alarm is a prediction above threshold not
    IoU>0.5-matched to a ground-truth box in its frame.
    """
    import numpy as np
    hours = sum(f["hours"] for f in frame_preds)
    total_gt = sum(len(f["gt"]) for f in frame_preds)

    scored: list[tuple[float, bool]] = []  # (conf, is_true_positive)
    for f in frame_preds:
        matched = set()
        for p in sorted(f["preds"], key=lambda p: -p["conf"]):
            is_tp = False
            for gi, gt in enumerate(f["gt"]):
                if gi in matched:
                    continue
                if _iou(p["bbox"], gt) > 0.5:
                    matched.add(gi)
                    is_tp = True
                    break
            if p["conf"] >= 0.0:
                scored.append((p["conf"], is_tp))

    if total_gt == 0:
        return {"false_alarms_per_hour": None, "recall_at_target_far": None, "sample_hours": hours}

    best = None  # (recall, far) closest to but not above target
    for thresh in np.linspace(0.0, 1.0, 401):
        fps = sum(1 for c, is_tp in scored if c >= thresh and not is_tp)
        tp = [c for c, is_tp in scored if c >= thresh and is_tp]
        recall = (len(tp) / total_gt) if total_gt else 0.0
        far = fps / hours if hours > 0 else float("inf")
        if far <= target_far and (best is None or recall > best[0]):
            best = (recall, far, thresh)

    return {
        "false_alarms_per_hour": round(best[1], 3) if best else None,
        "recall_at_target_far": round(best[0], 4) if best else None,
    }


def per_class_gt_counts(benchmark_root: Path, class_ids: list[int]) -> tuple[dict, dict]:
    """Load ground-truth per class + per-image GT boxes (px xyxy) from the
    cleaned YOLO benchmark labels. Returns (gt_by_image, gt_counts).
    """
    images_dir = benchmark_root / "images"
    labels_dir = benchmark_root / "labels"
    gt_by_image: dict[str, list] = {}
    gt_counts = {i: 0 for i in class_ids}
    if labels_dir.is_dir():
        for lab in sorted(labels_dir.glob("*.txt")):
            stem = lab.stem
            gt_by_image.setdefault(stem, [])
            for line in lab.read_text().splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                c = int(float(parts[0]))
                cx, cy, bw, bh = map(float, parts[1:5])
                img = images_dir / f"{stem}.jpg"
                if not img.exists():
                    continue
                import PIL.Image
                with PIL.Image.open(img) as im:
                    w, h = im.size
                x0, y0 = (cx - bw / 2) * w, (cy - bh / 2) * h
                x1, y1 = (cx + bw / 2) * w, (cy + bh / 2) * h
                gt_by_image[stem].append((c, [x0, y0, x1, y1]))
                gt_counts[c] += 1
    return gt_by_image, gt_counts


def static_per_class_recall(model, benchmark_root: Path, device, conf: float = 0.25) -> dict:
    """Per-class recall + ground-truth counts on the frozen benchmark, computed
    at a practical operating confidence (not the lenient default). The gate's
    per-class floor is tiered by `gt_count`, so we record both honestly."""
    images_dir = benchmark_root / "images"
    class_ids = sorted(int(k) for k in (model.names or {}).keys())
    if not class_ids:
        raise ValueError("model exposes no class names; cannot do per-class recall")

    gt_by_image, gt_counts = per_class_gt_counts(benchmark_root, class_ids)
    matched = {c: 0 for c in class_ids}

    for img in sorted(images_dir.glob("*.jpg")):
        gt = gt_by_image.get(img.stem, [])
        if not gt:
            continue
        res = model.predict(str(img), conf=conf, iou=0.5, verbose=False, device=device)[0]
        if res.boxes is None:
            continue
        used = set()
        for xyxy, c in zip(res.boxes.xyxy.tolist(), res.boxes.cls.tolist()):
            c = int(c)
            if c not in gt_counts or gt_counts[c] == 0:
                continue
            for gi, (gc, gb) in enumerate(gt):
                if gi in used or gc != c:
                    continue
                if _iou(xyxy, gb) > 0.5:
                    matched[c] += 1
                    used.add(gi)
                    break

    per_class = {}
    for c in class_ids:
        name = model.names.get(c) if hasattr(model, "names") else str(c)
        per_class[str(name)] = {
            "recall": round(matched[c] / gt_counts[c], 4) if gt_counts.get(c) else None,
            "gt_count": int(gt_counts.get(c, 0)),
            "conf": conf,
        }
    return per_class


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="frozen benchmark manifest (json)")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--corpus-manifest")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--latency-iters", type=int, default=100)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import yaml
    manifest = json.loads(Path(args.manifest).read_text())
    benchmark_root = Path(manifest["benchmark_root"])
    val_yaml = benchmark_root / "val.yaml"
    if not val_yaml.exists():
        sys.exit(f"val.yaml not found in benchmark root: {val_yaml}")

    from ultralytics import YOLO
    model = YOLO(args.weights)

    # Static benchmark metrics.
    metrics = model.val(data=str(val_yaml), device=args.device, verbose=False)
    per_class = static_per_class_recall(model, benchmark_root, args.device)

    # Latency on the declared device (p95 over iters).
    import torch
    x = torch.randn(1, 3, 640, 640).to(args.device) if args.device != "cpu" else torch.randn(1, 3, 640, 640)
    lats = []
    for _ in range(5):
        model.predict(source=x, verbose=False, device=args.device)
    for _ in range(args.latency_iters):
        t0 = time.perf_counter()
        model.predict(source=x, verbose=False, device=args.device)
        lats.append((time.perf_counter() - t0) * 1000)
    lats.sort()
    p95 = lats[int(len(lats) * 0.95)]

    # Product metrics require the temporal corpus.
    product = {"false_alarms_per_hour": None, "recall_at_target_far": None}
    if args.corpus_manifest:
        corpus = build_temporal_corpus(json.loads(Path(args.corpus_manifest).read_text()))
        frame_preds = sweep_thresholds(model, corpus, args.device)
        product = compute_product_metrics(frame_preds, TARGET_FAR_PER_HOUR)

    import numpy as np  # already imported in sweep; ensure available

    def _scalar(v):
        # ultralytics DetMetrics may expose per-class arrays for p/r
        if isinstance(v, (list, tuple)):
            return float(np.mean(v)) if len(v) else 0.0
        try:
            arr = np.asarray(v)
            if arr.ndim == 0:
                return float(arr)
            return float(arr.mean()) if arr.size else 0.0
        except Exception:
            return float(v)

    eval_json = {
        "benchmark": manifest["benchmark_name"],
        "benchmark_hash": manifest["benchmark_hash"],
        "map_50": float(metrics.box.map50),
        "map_50_95": float(metrics.box.map),
        "precision_at_conf": _scalar(metrics.box.p),
        "recall_at_conf": _scalar(metrics.box.r),
        "recall_at_target_far": product["recall_at_target_far"],
        "false_alarms_per_hour": product["false_alarms_per_hour"],
        "device": args.device,
        "latency_p95_ms": round(p95, 2),
        "per_class": per_class,
        "per_slice": {},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(eval_json, indent=2, sort_keys=True))
    print(json.dumps(eval_json, indent=2, sort_keys=True))
    if product["false_alarms_per_hour"] is None:
        print(
            "WARNING: no temporal corpus supplied; false_alarms_per_hour and "
            "recall_at_target_far are null and the promotion gate will REJECT "
            "this eval. The product metric is not mAP; measure it."
        )


if __name__ == "__main__":
    main()