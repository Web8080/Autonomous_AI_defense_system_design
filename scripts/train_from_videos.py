#!/usr/bin/env python3
"""Build a YOLO train set from real MP4 clips + sidecar GT, then fine-tune.

Author: Victor.I

Uses data/videos/*.mp4 (+ .gt.json) produced from VisDrone aerial sequences —
real human/vehicle footage, not synthetic L2. Writes data/video-yolo/ and
optionally runs a short Ultralytics fine-tune into data/models/visdrone-video.pt.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# VisDrone effective class order (must match dataset.yaml / prepare.py)
CLASS_TO_ID = {
    "pedestrian": 0,
    "people": 1,
    "bicycle": 2,
    "car": 3,
    "van": 4,
    "truck": 5,
    "tricycle": 6,
    "awning-tricycle": 7,
    "bus": 8,
    "motor": 9,
}


def extract_clip(mp4: Path, gt_path: Path, images_dir: Path, labels_dir: Path, prefix: str) -> int:
    import cv2
    from PIL import Image
    import numpy as np

    payload = json.loads(gt_path.read_text()) if gt_path.is_file() else {"frames": []}
    gt_frames = payload.get("frames") or []
    cap = cv2.VideoCapture(str(mp4))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {mp4}")
    n = 0
    idx = 0
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        im = Image.fromarray(rgb)
        w, h = im.size
        stem = f"{prefix}_{idx:05d}"
        im.save(images_dir / f"{stem}.jpg", quality=92)
        lines: list[str] = []
        anns = []
        if idx < len(gt_frames):
            anns = gt_frames[idx].get("annotations") or []
        for ann in anns:
            name = ann.get("class_name", "")
            if name not in CLASS_TO_ID:
                continue
            bbox = ann.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x0, y0, x1, y1 = bbox
            bw = max(x1 - x0, 1.0)
            bh = max(y1 - y0, 1.0)
            cx = (x0 + x1) / 2.0 / w
            cy = (y0 + y1) / 2.0 / h
            nw = bw / w
            nh = bh / h
            lines.append(f"{CLASS_TO_ID[name]} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
        (labels_dir / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        n += 1
        idx += 1
    cap.release()
    return n


def write_data_yaml(out_root: Path, train_rel: str, val_rel: str) -> Path:
    yaml_path = out_root / "data.yaml"
    names = {v: k for k, v in CLASS_TO_ID.items()}
    # ultralytics wants names as list indexed by id
    name_list = [names[i] for i in range(10)]
    body = (
        f"path: {out_root.resolve()}\n"
        f"train: {train_rel}\n"
        f"val: {val_rel}\n"
        f"nc: 10\n"
        f"names: {name_list}\n"
    )
    yaml_path.write_text(body)
    return yaml_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-dir", default=str(ROOT / "data/videos"))
    parser.add_argument("--out-root", default=str(ROOT / "data/video-yolo"))
    parser.add_argument("--weights", default=str(ROOT / "data/models/visdrone-yolov8n.pt"))
    parser.add_argument("--artifact", default=str(ROOT / "data/models/visdrone-video.pt"))
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--val-frac", type=float, default=0.2)
    args = parser.parse_args()

    video_dir = Path(args.video_dir)
    out_root = Path(args.out_root)
    train_img = out_root / "train" / "images"
    train_lab = out_root / "train" / "labels"
    val_img = out_root / "val" / "images"
    val_lab = out_root / "val" / "labels"
    for d in (train_img, train_lab, val_img, val_lab):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    mp4s = sorted(video_dir.glob("site-aerial-*.mp4"))
    if not mp4s:
        mp4s = sorted(p for p in video_dir.glob("*.mp4") if "railway" not in p.name or True)
    # Prefer aerial clips; keep railway alias out of duplicate extract if same bytes
    seen_size: set[int] = set()
    total = 0
    for mp4 in mp4s:
        sz = mp4.stat().st_size
        if sz in seen_size and "railway" in mp4.name:
            continue
        seen_size.add(sz)
        gt = mp4.with_suffix(".gt.json")
        n = extract_clip(mp4, gt, train_img, train_lab, mp4.stem)
        print(f"{mp4.name}: {n} frames")
        total += n

    # Hold out a fraction of train images as val
    imgs = sorted(train_img.glob("*.jpg"))
    rng = random.Random(42)
    rng.shuffle(imgs)
    n_val = max(1, int(len(imgs) * args.val_frac))
    for im in imgs[:n_val]:
        lab = train_lab / f"{im.stem}.txt"
        shutil.move(str(im), val_img / im.name)
        if lab.exists():
            shutil.move(str(lab), val_lab / lab.name)

    yaml_path = write_data_yaml(out_root, "train/images", "val/images")
    print(f"dataset frames≈{total} train={len(list(train_img.glob('*.jpg')))} val={len(list(val_img.glob('*.jpg')))}")
    print(f"data yaml: {yaml_path}")

    if args.skip_train:
        return

    if not Path(args.weights).is_file():
        print(f"missing weights {args.weights}", file=sys.stderr)
        sys.exit(1)

    from ultralytics import YOLO

    model = YOLO(args.weights)
    model.train(
        data=str(yaml_path),
        epochs=args.epochs,
        batch=args.batch,
        imgsz=640,
        device=args.device,
        project=str(ROOT / "runs/detect"),
        name="visdrone-video",
        exist_ok=True,
        patience=20,
        lr0=0.005,
    )
    best = ROOT / "runs/detect/visdrone-video/weights/best.pt"
    if not best.is_file():
        # ultralytics sometimes nests runs/detect/runs/detect
        alts = list((ROOT / "runs/detect").rglob("visdrone-video/**/best.pt"))
        if alts:
            best = alts[0]
    if best.is_file():
        Path(args.artifact).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, args.artifact)
        print(f"copied {best} -> {args.artifact}")
    else:
        print("WARN: best.pt not found; training may have failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
