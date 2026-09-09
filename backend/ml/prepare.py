"""Dataset preparation: raw VisDrone -> clean YOLO format (feature engineering).

Feature engineering for an object-detection product is mostly about the *data*:
exceptional-class handling, class cardinality decisions, label sanity, and a
recorded class distribution so training choices can be audited later.

VisDrone-DET raw annotations are `x1,y1,w,h,score,category,truncation,occlusion`.
This step:

1. Drops category 0 (`ignored regions`) and category 11 (`other`):
   the former is an ignore-region directive, not a detection; the latter is too
   uninformative to be a product alarm class. 10 effective classes remain.
2. Remaps categories 1..10 -> 0..9.
3. Clamps boxes to image bounds (VisDrone has known out-of-bounds boxes) and
   drops empty/zero-area artifacts.
4. Writes YOLO `images/`+`labels/` trees, an effective dataset yaml, and a
   per-class distribution report.

Idempotent: re-running on an already-cleaned tree is a no-op.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAW_CLASSES = {
    0: "ignored regions",
    1: "pedestrian",
    2: "people",
    3: "bicycle",
    4: "car",
    5: "van",
    6: "truck",
    7: "tricycle",
    8: "awning-tricycle",
    9: "bus",
    10: "motor",
    11: "other",
}

# Effective classes: VisDrone names, new id = raw_id - 1 for raw_id in 1..10.
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


def _parse_label_line(line: str) -> tuple[float, float, float, float, int, float]:
    parts = [p.strip() for p in line.strip().replace(",", " ").split()]
    if len(parts) < 6:
        raise ValueError(f"malformed line: {line!r}")
    x1, y1, w, h = (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    score = float(parts[4])
    category = int(float(parts[5]))
    if w <= 0 or h <= 0:
        raise ValueError("non-positive bbox size")
    return x1, y1, w, h, category, score


def convert_annotation(src: Path, img_w: int, img_h: int) -> list[str] | None:
    """Return YOLO lines for one raw annotation file, or None if it should be
    dropped entirely (no usable detections after filtering)."""
    out: list[str] = []
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        try:
            x1, y1, w, h, category, score = _parse_label_line(line)
        except ValueError:
            continue  # malformed lines are dropped, counted nowhere: keep clean
        if score <= 0:
            continue
        if category not in RAW_CLASSES:
            continue
        if category == 0 or category == 11:  # ignore regions / other
            continue
        # Clamp to the image plane and drop zero-area leftovers.
        x2 = min(x1 + w, img_w)
        y2 = min(y1 + h, img_h)
        x1c, y1c = max(x1, 0.0), max(y1, 0.0)
        if x2 - x1c <= 0 or y2 - y1c <= 0:
            continue
        cx = (x1c + x2) / 2 / img_w
        cy = (y1c + y2) / 2 / img_h
        bw = (x2 - x1c) / img_w
        bh = (y2 - y1c) / img_h
        out.append(f"{category - 1} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")
    return out or None


def image_dimensions(img: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(img) as im:
        return im.size  # (w, h)


def process_split(split: Path, out_root: Path, report: dict) -> dict:
    images_dir = split / "images"
    ann_dir = split / "annotations"
    (out_root / "images").mkdir(parents=True, exist_ok=True)
    (out_root / "labels").mkdir(parents=True, exist_ok=True)

    classes = Counter()
    kept = dropped = 0
    image_sizes = Counter()

    for ann in sorted(ann_dir.glob("*.txt")):
        img = images_dir / f"{ann.stem}.jpg"
        if not img.exists():
            dropped += 1
            continue
        w, h = image_dimensions(img)
        image_sizes[(w, h)] += 1
        lines = convert_annotation(ann, w, h)
        if not lines:
            dropped += 1
            continue
        shutil.copy(img, out_root / "images" / img.name)
        (out_root / "labels" / ann.name).write_text("".join(lines))
        kept += 1
        for ln in lines:
            classes[int(ln.split()[0])] += 1

    objects = sum(classes.values())
    report[f"{split.name}_kept_images"] = kept
    report[f"{split.name}_dropped_images"] = dropped
    report[f"{split.name}_objects"] = objects
    report[f"{split.name}_mean_objects_per_image"] = round(objects / kept, 3) if kept else 0
    report[f"{split.name}_class_counts"] = {
        EFFECTIVE_NAMES.get(c, f"class-{c}"): n for c, n in sorted(classes.items())
    }
    report[f"{split.name}_image_sizes"] = {
        f"{w}x{h}": n for (w, h), n in sorted(image_sizes.items(), key=lambda kv: -kv[1])
    }
    return classes


def write_effective_yaml(path: Path, train_root: str, val_root: str) -> None:
    lines = [
        "# Effective VisDrone-DET dataset (raw category 0 'ignored regions' and",
        "# 11 'other' dropped; raw 1..10 remapped to 0..9). Generated by ml/prepare.py.",
        f"path: {path.parent.resolve()}",
        "train: train",
        "val: val",
        "names:",
    ]
    for i, name in EFFECTIVE_NAMES.items():
        lines.append(f"  {i}: {name}")
    path.write_text("\n".join(lines) + "\n")


def write_split_yaml(split_root: Path) -> None:
    """Per-split dataset yaml so `model.val(data=<split>/<split>.yaml)` and
    training both get a self-contained, ultralytics-valid file."""
    key = split_root.name
    lines = [
        "# Per-split dataset file generated by ml/prepare.py.",
        f"path: {split_root.resolve()}",
        f"val: .",
        "train: ../train",
        "names:",
    ]
    for i, name in EFFECTIVE_NAMES.items():
        lines.append(f"  {i}: {name}")
    (split_root / f"{key}.yaml").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", default="data/visdrone-raw",
                        help="dir containing VisDrone2019-DET-train/ and ...-val/")
    parser.add_argument("--out-root", default="data/visdrone-yolo")
    args = parser.parse_args()

    raw_root = Path(args.raw_root)
    out_root = Path(args.out_root)
    if not (raw_root / "VisDrone2019-DET-train").is_dir():
        sys.exit(f"raw train split not found under {raw_root}")

    report: dict = {}
    for split in ("VisDrone2019-DET-train", "VisDrone2019-DET-val"):
        key = "train" if "train" in split else "val"
        report[f"split_{key}_source"] = str(raw_root / split)
        process_split(raw_root / split, out_root / key, report)

    write_effective_yaml(
        out_root / "visdrone_effective.yaml",
        train_root=str(out_root / "train"),
        val_root=str(out_root / "val"),
    )
    for key in ("train", "val"):
        write_split_yaml(out_root / key)
    (out_root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()