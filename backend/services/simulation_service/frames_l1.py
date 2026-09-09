"""Layer 1: real-footage replay through the production pipeline.

Drives the temporal corpus (sequence-aligned VisDrone frames at nominal 5 fps —
the same frozen benchmark used for offline evaluation) through the live product
path: each frame is encoded exactly like a camera-gateway upload and published
to `inference.frames`. What the product detects is then scored against the
frame's real annotations.

This is the honest layer-1 answer to "use the trained model on real footage":
it is genuinely real aerial data, exercised through the real stack, live.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import PIL.Image

from scoring import GTBox

MAX_W = 640
JPEG_Q = 80


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
        self._cache: dict[int, tuple[str, int, int]] = {}

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

    def _encoded(self, idx: int) -> tuple[str, int, int]:
        if idx in self._cache:
            return self._cache[idx]
        payload = self.frames[idx]
        with PIL.Image.open(payload["image"]) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            ratio = MAX_W / im.width
            if ratio < 1:
                h = round(im.height * ratio) or 1
                im = im.resize((MAX_W, h))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_Q)
            b64 = base64.b64encode(buf.getvalue()).decode()
            self._cache[idx] = (b64, im.width, im.height)
        return self._cache[idx]

    def frame(self, idx: int) -> tuple[str, int, int, list[GTBox]]:
        """Return (image_b64, width, height, normalized_gt)."""
        self.load()
        payload = self.frames[idx]
        b64, w, h = self._encoded(idx)
        gts: list[GTBox] = []
        for ann in payload["annotations"]:
            x0, y0, x1, y1 = ann["bbox"]
            gts.append(
                GTBox(
                    class_name=ann.get("class_name", "unknown"),
                    bbox=[x0 / w, y0 / h, x1 / w, y1 / h],
                )
            )
        return b64, w, h, gts

    def meta(self, idx: int) -> dict:
        self.load()
        payload = self.frames[idx]
        return {"image": payload["image"], "source": "visdrone-corpus", "frame": idx}