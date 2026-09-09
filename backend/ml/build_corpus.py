"""Temporal flight-corpus builder for the product metrics.

`false_alarms_per_hour` and `recall_at_target_far` cannot be measured on a
folder of independent images. They need continuous flight segments with a
frame rate. VisDrone names encode sequence + frame (`0000001_02999_d_...jpg`),
so we group the cleared val frames by sequence and order them by frame index to
form pseudo-flight segments at a nominal 5 fps (AISKYEYE samples ~5 fps from
the source footage).

Honesty note: these are pseudo-flights reconstructed from a benchmark that is
station-per-segment and weather/lighting-invariant. The numbers they produce
are an *indicative* FAPFH on never-trained aerial data, not a certification of
field performance at a real site. That distinction stays written everywhere.

Usage:
    python ml/build_corpus.py --val-root data/visdrone-yolo/val
                              --out data/visdrone-yolo/corpus/corpus.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

NOMINAL_FPS = 5.0

# Effective class ids -> names (must match ml.prepare.EFFECTIVE_NAMES).
EFFECTIVE_NAMES = {
    0: "pedestrian",
    1: "people",
    2: "bicycle",
    3: "car",
    4: "van",
    5: "truck",
    6: "tricycle",
    7: "awning-tricycle",
    8: "bus",
    9: "motor",
}


def frame_gt_boxes(labels_dir: Path, stem: str, img_w: int, img_h: int) -> list[dict]:
    lab = labels_dir / f"{stem}.txt"
    if not lab.exists():
        return []
    out = []
    for line in lab.read_text().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cls_id = int(float(parts[0]))
        cx, cy, bw, bh = map(float, parts[1:5])
        x0 = (cx - bw / 2) * img_w
        y0 = (cy - bh / 2) * img_h
        x1 = (cx + bw / 2) * img_w
        y1 = (cy + bh / 2) * img_h
        out.append({
            "bbox": [x0, y0, x1, y1],
            "ignore": False,
            "class_name": EFFECTIVE_NAMES.get(cls_id, f"class_{cls_id}"),
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--val-root", default="data/visdrone-yolo/val")
    parser.add_argument("--out", default="data/visdrone-yolo/corpus/corpus.json")
    args = parser.parse_args()

    val_root = Path(args.val_root)
    images_dir = val_root / "images"
    labels_dir = val_root / "labels"

    import PIL.Image
    frames_by_seq: dict[str, list] = defaultdict(list)
    for img in sorted(images_dir.glob("*.jpg")):
        parts = img.stem.split("_")
        seq = parts[0]
        frame_idx = int(parts[1])
        with PIL.Image.open(img) as im:
            w, h = im.size
        frames_by_seq[seq].append({
            "image": str(img.resolve()),
            "frame": frame_idx,
            "annotations": frame_gt_boxes(labels_dir, img.stem, w, h),
        })

    segments = []
    for seq in sorted(frames_by_seq):
        frames = sorted(frames_by_seq[seq], key=lambda f: f["frame"])
        segments.append({
            "seq": seq,
            "fps": NOMINAL_FPS,
            "title": f"visdrone-val-seq-{seq}",
            "hours": round(len(frames) / NOMINAL_FPS / 3600.0, 4),
            "frames": [{k: f[k] for k in ("image", "annotations")} for f in frames],
        })

    corpus = {
        "title": "visdrone-val-pseudo-flights",
        "description": (
            "Pseudo-flight segments reconstructed from the frozen VisDrone benchmark "
            "(sequence-aligned frames at nominal 5fps). Product metrics only; "
            "never trained on."
        ),
        "nominal_fps": NOMINAL_FPS,
        "segments": segments,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(corpus, indent=2))
    hrs = corpus["segments"][0]["hours"] if segments else 0.0
    print(f"segments={len(segments)} frames={sum(len(s['frames']) for s in segments)}")
    print(f"mean hours per segment={hrs:.4f}\nwrote {out}")


if __name__ == "__main__":
    main()