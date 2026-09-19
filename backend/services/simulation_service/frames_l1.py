"""Layer 1: real aerial footage through the production pipeline.

Author: Victor.I

Two sources share the same pump contract:
  - CorpusSource: VisDrone still sequences (pseudo-flights) with GT labels
  - VideoSource: MP4 / H.264 site or drone clips (+ optional sidecar GT JSON)

Frames are JPEG-encoded like a camera gateway and published to
`inference.frames`. Detections return on `inference.detections` and are
scored against ground truth when available.
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import PIL.Image

from scoring import GTBox

MAX_W = 640
JPEG_Q = 80

# Host paths in corpus.json may not exist inside Docker. Prefer SIM_IMAGE_ROOT
# (directory of JPGs) and fall back to basename lookup under the data root.
IMAGE_ROOT = Path(os.getenv("SIM_IMAGE_ROOT", "")).expanduser() if os.getenv("SIM_IMAGE_ROOT") else None
DATA_ROOT = Path(os.getenv("SIM_DATA_ROOT", "")).expanduser() if os.getenv("SIM_DATA_ROOT") else None
VIDEO_DIR = Path(os.getenv("SIM_VIDEO_DIR", "data/videos")).expanduser()


def resolve_image_path(raw: str) -> Path:
    """Resolve a corpus image path that may be absolute (host) or relative."""
    p = Path(raw)
    if p.is_file():
        return p
    name = p.name
    candidates: list[Path] = []
    if IMAGE_ROOT:
        candidates.append(IMAGE_ROOT / name)
        candidates.append(IMAGE_ROOT / p.name)
    if DATA_ROOT:
        candidates.append(DATA_ROOT / "val" / "images" / name)
        if not p.is_absolute():
            candidates.append(DATA_ROOT / raw)
    # last resort: relative to cwd
    candidates.append(Path("data/visdrone-yolo/val/images") / name)
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        f"image not found: {raw!r} (set SIM_IMAGE_ROOT or SIM_DATA_ROOT)"
    )


def _encode_rgb(im: PIL.Image.Image) -> tuple[str, int, int, float]:
    """Resize for Kafka payload; return b64, out_w, out_h, scale vs original width."""
    if im.mode != "RGB":
        im = im.convert("RGB")
    orig_w, orig_h = im.width, im.height
    ratio = MAX_W / orig_w if orig_w > MAX_W else 1.0
    if ratio < 1:
        h = round(orig_h * ratio) or 1
        im = im.resize((MAX_W, h))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=JPEG_Q)
    return base64.b64encode(buf.getvalue()).decode(), im.width, im.height, ratio


def _gt_from_anns(annotations: list[dict], orig_w: int, orig_h: int) -> list[GTBox]:
    """Normalize GT using original pixel size (not the resized JPEG size)."""
    gts: list[GTBox] = []
    if orig_w <= 0 or orig_h <= 0:
        return gts
    for ann in annotations:
        if ann.get("ignore"):
            continue
        bbox = ann.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = bbox
        gts.append(
            GTBox(
                class_name=str(ann.get("class_name", "unknown")),
                bbox=[x0 / orig_w, y0 / orig_h, x1 / orig_w, y1 / orig_h],
            )
        )
    return gts


class CorpusSource:
    """Loads the corpus manifest and serves frames + normalized GT on demand."""

    def __init__(
        self,
        manifest_path: str | Path,
        sequence: str | None = None,
        start_offset: int = 0,
        frames_limit: int | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.sequence = sequence
        self.start_offset = start_offset
        self.frames_limit = frames_limit
        self.frames: list[dict] = []
        self._cache: dict[int, tuple[str, int, int, int, int]] = {}

    def load(self) -> "CorpusSource":
        if self.frames:
            return self
        corpus = json.loads(self.manifest_path.read_text())
        frames: list[dict] = []
        for seg in corpus["segments"]:
            if self.sequence and self.sequence != seg["seq"]:
                continue
            for fr in seg["frames"]:
                frames.append(fr)
        frames.sort(key=lambda f: str(f["image"]))
        frames = frames[self.start_offset :]
        if self.frames_limit is not None:
            frames = frames[: self.frames_limit]
        self.frames = frames
        return self

    @property
    def sequence_ids(self) -> list[str]:
        corpus = json.loads(self.manifest_path.read_text())
        return [seg["seq"] for seg in corpus["segments"]]

    def __len__(self) -> int:
        self.load()
        return len(self.frames)

    def _encoded(self, idx: int) -> tuple[str, int, int, int, int]:
        if idx in self._cache:
            return self._cache[idx]
        payload = self.frames[idx]
        path = resolve_image_path(payload["image"])
        with PIL.Image.open(path) as im:
            orig_w, orig_h = im.width, im.height
            b64, w, h, _ = _encode_rgb(im)
            self._cache[idx] = (b64, w, h, orig_w, orig_h)
        return self._cache[idx]

    def frame(self, idx: int) -> tuple[str, int, int, list[GTBox]]:
        self.load()
        payload = self.frames[idx]
        b64, w, h, orig_w, orig_h = self._encoded(idx)
        gts = _gt_from_anns(payload.get("annotations") or [], orig_w, orig_h)
        return b64, w, h, gts

    def meta(self, idx: int) -> dict:
        self.load()
        payload = self.frames[idx]
        return {"image": payload["image"], "source": "visdrone-corpus", "frame": idx}


class VideoSource:
    """Decode an MP4 into frames; optional sidecar `{stem}.gt.json` for GT.

    Sidecar shape:
      {"fps": 5, "frames": [{"annotations": [{"class_name", "bbox": [x0,y0,x1,y1]}]}, ...]}
    bbox is in original video pixel coordinates.
    """

    def __init__(
        self,
        video_path: str | Path,
        start_offset: int = 0,
        frames_limit: int | None = None,
        gt_path: str | Path | None = None,
    ) -> None:
        self.video_path = Path(video_path)
        self.start_offset = start_offset
        self.frames_limit = frames_limit
        self.gt_path = Path(gt_path) if gt_path else self.video_path.with_suffix(".gt.json")
        self._frames_b64: list[tuple[str, int, int]] = []
        self._orig_sizes: list[tuple[int, int]] = []
        self._gt: list[list[dict]] = []
        self._loaded = False

    def load(self) -> "VideoSource":
        if self._loaded:
            return self
        if not self.video_path.is_file():
            raise FileNotFoundError(f"video not found: {self.video_path}")
        gt_frames: list[list[dict]] = []
        if self.gt_path.is_file():
            payload = json.loads(self.gt_path.read_text())
            gt_frames = [f.get("annotations") or [] for f in payload.get("frames") or []]

        try:
            import cv2  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "opencv-python-headless is required for MP4 L1; "
                "pip install opencv-python-headless"
            ) from exc

        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video: {self.video_path}")
        idx = 0
        kept = 0
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            if idx < self.start_offset:
                idx += 1
                continue
            if self.frames_limit is not None and kept >= self.frames_limit:
                break
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            im = PIL.Image.fromarray(rgb)
            orig_w, orig_h = im.width, im.height
            b64, w, h, _ = _encode_rgb(im)
            self._frames_b64.append((b64, w, h))
            self._orig_sizes.append((orig_w, orig_h))
            anns = gt_frames[idx] if idx < len(gt_frames) else []
            self._gt.append(anns)
            kept += 1
            idx += 1
        cap.release()
        self._loaded = True
        return self

    def __len__(self) -> int:
        self.load()
        return len(self._frames_b64)

    def frame(self, idx: int) -> tuple[str, int, int, list[GTBox]]:
        self.load()
        b64, w, h = self._frames_b64[idx]
        orig_w, orig_h = self._orig_sizes[idx]
        gts = _gt_from_anns(self._gt[idx] if idx < len(self._gt) else [], orig_w, orig_h)
        return b64, w, h, gts

    def meta(self, idx: int) -> dict:
        return {
            "source": "mp4",
            "video": str(self.video_path.name),
            "frame": idx,
            "has_gt": bool(self._gt and idx < len(self._gt) and self._gt[idx]),
        }


def list_videos(video_dir: Path | None = None) -> list[dict]:
    root = video_dir or VIDEO_DIR
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(root.glob("*.mp4")):
        gt = p.with_suffix(".gt.json")
        out.append(
            {
                "id": p.stem,
                "file": p.name,
                "path": str(p),
                "has_gt": gt.is_file(),
            }
        )
    return out


def resolve_video(video_id_or_file: str, video_dir: Path | None = None) -> Path:
    root = video_dir or VIDEO_DIR
    direct = Path(video_id_or_file)
    if direct.is_file():
        return direct
    for cand in (root / video_id_or_file, root / f"{video_id_or_file}.mp4"):
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"video not found: {video_id_or_file} under {root}")
