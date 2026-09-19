#!/usr/bin/env python3
"""Build MP4 site-footage clips from VisDrone corpus sequences.

Author: Victor.I

Turns sequence-aligned JPEG pseudo-flights into real H.264 MP4 files the
simulation L1 VideoSource can play, with a sidecar `.gt.json` so scoring still
works. This is the honest path to "real human video" until customer site
footage is available — drop your own MP4s into data/videos/ the same way.

Usage:
    python scripts/build_visdrone_mp4s.py
    python scripts/build_visdrone_mp4s.py --sequences 0000001,0000002 --max-seqs 5
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve_image(raw: str, image_root: Path) -> Path:
    p = Path(raw)
    if p.is_file():
        return p
    cand = image_root / p.name
    if cand.is_file():
        return cand
    raise FileNotFoundError(raw)


def encode_sequence(
    frames: list[dict],
    out_mp4: Path,
    image_root: Path,
    fps: float,
    tmp_dir: Path,
) -> int:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for old in tmp_dir.glob("frame_*.jpg"):
        old.unlink()
    gt_frames: list[dict] = []
    n = 0
    for i, fr in enumerate(frames):
        src = resolve_image(fr["image"], image_root)
        dest = tmp_dir / f"frame_{i:05d}.jpg"
        dest.write_bytes(src.read_bytes())
        gt_frames.append({"annotations": fr.get("annotations") or []})
        n += 1
    if n == 0:
        return 0
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    pattern = str(tmp_dir / "frame_%05d.jpg")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-i",
        pattern,
        # x264 requires even width/height
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "23",
        str(out_mp4),
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        # Fallback: OpenCV writer if ffmpeg is missing
        import cv2  # type: ignore
        import PIL.Image
        import numpy as np

        first = PIL.Image.open(tmp_dir / "frame_00000.jpg")
        w, h = first.size
        writer = cv2.VideoWriter(
            str(out_mp4),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (w, h),
        )
        for i in range(n):
            im = PIL.Image.open(tmp_dir / f"frame_{i:05d}.jpg").convert("RGB")
            bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
            writer.write(bgr)
        writer.release()
    sidecar = {
        "title": out_mp4.stem,
        "fps": fps,
        "source": "visdrone-val-pseudo-flight",
        "frames": gt_frames,
    }
    out_mp4.with_suffix(".gt.json").write_text(json.dumps(sidecar))
    return n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=str(ROOT / "data/visdrone-yolo/corpus/corpus.json"))
    parser.add_argument("--image-root", default=str(ROOT / "data/visdrone-yolo/val/images"))
    parser.add_argument("--out-dir", default=str(ROOT / "data/videos"))
    parser.add_argument("--sequences", default="", help="comma-separated seq ids; default = first N")
    parser.add_argument("--max-seqs", type=int, default=8)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument(
        "--alias-railway",
        default="0000001",
        help="also copy this seq MP4 to site-railway-0000001.mp4 for agent replay",
    )
    args = parser.parse_args()

    corpus_path = Path(args.corpus)
    if not corpus_path.is_file():
        print(f"corpus missing: {corpus_path}", file=sys.stderr)
        sys.exit(1)
    corpus = json.loads(corpus_path.read_text())
    image_root = Path(args.image_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = out_dir / ".tmp_frames"

    want = {s.strip() for s in args.sequences.split(",") if s.strip()}
    segs = corpus["segments"]
    if want:
        segs = [s for s in segs if s["seq"] in want]
    else:
        segs = segs[: args.max_seqs]

    built = 0
    for seg in segs:
        seq = seg["seq"]
        out_mp4 = out_dir / f"site-aerial-{seq}.mp4"
        n = encode_sequence(
            seg["frames"],
            out_mp4,
            image_root,
            args.fps,
            tmp_root / seq,
        )
        print(f"{seq}: {n} frames -> {out_mp4.name}")
        built += 1
        if seq == args.alias_railway:
            alias = out_dir / "site-railway-0000001.mp4"
            alias.write_bytes(out_mp4.read_bytes())
            gt_src = out_mp4.with_suffix(".gt.json")
            if gt_src.is_file():
                alias.with_suffix(".gt.json").write_text(gt_src.read_text())
            print(f"  alias -> {alias.name} (agent replay CV)")

    # cleanup tmp
    import shutil

    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    print(f"built {built} MP4(s) under {out_dir}")


if __name__ == "__main__":
    main()
