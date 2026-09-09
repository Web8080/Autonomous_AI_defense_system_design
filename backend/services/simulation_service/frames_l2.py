"""Layer 2: synthetic scene composition through the production pipeline.

Procedurally composes an aerial drone view (grass/gravel ground with roads and
scripted people + vehicles moving along paths) and publishes it to the real
`inference.frames` topic. Ground truth is exact (computed from the scripted
world), so runtime precision/recall against the trained model is meaningful —
and, unlike layer 1, layer 2 exercises the model on *unbounded* novel
compositions: this is what powers rare-class augmentation and what-if scenarios
(trucks/cars at a depot, crowds in a market) before we ever fly.

Deterministic given a seed: identical scenario -> identical frames -> the same
frames a real camera would feed on replay.
"""
from __future__ import annotations

import base64
import io
import math
import random
from dataclasses import dataclass, field
from typing import Iterable

import PIL.Image as PILImage
from PIL import ImageDraw

from scoring import GTBox

W, H = 960, 540
_PALETTES = {
    "person": ["#e2574c", "#f0a04b", "#4cb3a0", "#8f6bd6", "#d98cb3", "#e8e3d5"],
    "vehicle": ["#3f86e0", "#d64550", "#2f6b3c", "#e0c341", "#5a5f66", "#c27a3d"],
}

GROUND_PALETTE = {
    "grass": ["#3a5f29", "#33521f", "#41692d", "#2f4d20", "#446f30"],
    "gravel": ["#8b8577", "#7d7668", "#948d7e", "#847d6f", "#77706a"],
}


def _interp(p: tuple[float, float], q: tuple[float, float], t: float) -> tuple[float, float]:
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _path_pos(points: list[tuple[float, float]], dist: float) -> tuple[tuple[float, float], float]:
    """Return (pos, heading_radians) moving along `points` at accumulated `dist` m."""
    segs: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    total = 0.0
    for p, q in zip(points, points[1:]):
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        total += length
        segs.append((p, q, length))
    if total <= 0:
        return points[0], 0.0
    remain = dist % total
    heading = 0.0
    for p, q, length in segs:
        if length <= 0:
            continue
        if remain <= length:
            pos = _interp(p, q, remain / length)
            heading = math.atan2(q[1] - p[1], q[0] - p[0])
            return pos, heading
        remain -= length
    last = points[-2], points[-1]
    heading = math.atan2(last[1][1] - last[0][1], last[1][0] - last[0][0])
    return points[-1], heading


def _cum_length(points: list[tuple[float, float]]) -> float:
    return sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(points, points[1:]))


@dataclass
class SyntheticSpec:
    """Scripted world; determinism comes from the fixed seed."""

    world_w: float = 240.0
    world_h: float = 140.0
    ground: str = "grass"
    roads: list[dict] = field(default_factory=list)
    people: list[dict] = field(default_factory=list)
    vehicles: list[dict] = field(default_factory=list)
    seed: int = 7


SCENARIOS: dict[str, SyntheticSpec] = {
    "railway-yard": SyntheticSpec(
        world_w=240.0,
        world_h=140.0,
        ground="gravel",
        roads=[
            {"points": [(0, 70), (120, 70), (240, 85)], "width": 9.0, "color": "#4a443c"},
            {"points": [(60, 0), (60, 140)], "width": 6.0, "color": "#3c3731"},
            {"points": [(150, -10), (150, 60), (210, 80), (210, 140)], "width": 5.0, "color": "#57514a"},
        ],
        people=[
            {"path": [(10, 70), (40, 72), (80, 68)], "speed": 1.2},
            {"path": [(55, 30), (55, 80), (70, 90)], "speed": 1.0},
            {"path": [(150, 20), (150, 55), (160, 70)], "speed": 1.1},
            {"path": [(200, 140), (200, 90), (175, 75)], "speed": 0.9},
            {"path": [(30, 130), (45, 120), (60, 135)], "speed": 0.8},
            {"path": [(110, 120), (130, 90), (140, 110)], "speed": 1.4},
        ],
        vehicles=[
            {"path": [(0, 70), (240, 85)], "speed": 7.0, "bwh": (4.6, 2.2)},
            {"path": [(240, 70), (0, 70)], "speed": 5.0, "bwh": (4.6, 2.2)},
            {"path": [(150, -10), (150, 60), (210, 80)], "speed": 4.0, "bwh": (6.5, 2.4)},
            {"path": [(60, 140), (60, 0)], "speed": 5.5, "bwh": (4.2, 2.0)},
        ],
        seed=11,
    ),
    "market-square": SyntheticSpec(
        world_w=200.0,
        world_h=120.0,
        ground="grass",
        roads=[
            {"points": [(0, 60), (200, 60)], "width": 10.0, "color": "#6c6456"},
            {"points": [(100, 0), (100, 120)], "width": 8.0, "color": "#6c6456"},
        ],
        people=[
            {"path": [(20, 20), (80, 50), (150, 30)], "speed": 1.1},
            {"path": [(180, 20), (120, 80), (40, 70)], "speed": 1.3},
            {"path": [(30, 100), (70, 75), (120, 100)], "speed": 0.9},
            {"path": [(160, 90), (140, 60), (90, 95)], "speed": 1.0},
            {"path": [(50, 60), (110, 60), (150, 60)], "speed": 0.7},
            {"path": [(10, 10), (190, 10)], "speed": 1.5},
            {"path": [(10, 110), (190, 110)], "speed": 1.2},
            {"path": [(190, 110), (190, 10)], "speed": 1.1},
        ],
        vehicles=[
            {"path": [(0, 60), (200, 60)], "speed": 6.0, "bwh": (4.4, 2.0)},
            {"path": [(200, 60), (0, 60)], "speed": 5.0, "bwh": (4.4, 2.0)},
            {"path": [(100, 120), (100, 0)], "speed": 5.5, "bwh": (4.0, 1.9)},
        ],
        seed=23,
    ),
}


def _noise_tile(seed: int, gw: int, gh: int, ground: str) -> list[list[str]]:
    rng = random.Random(seed)
    palette = GROUND_PALETTE[ground]
    tile: list[list[str]] = []
    for _ in range(gh):
        row: list[str] = []
        for _ in range(gw):
            row.append(rng.choice(palette))
        tile.append(row)
    return tile


class SyntheticSource:
    """Renders scripted world frames; each frame is a fresh composition."""

    def __init__(self, spec: SyntheticSpec = SCENARIOS["railway-yard"], fps: float = 10.0) -> None:
        self.spec = spec
        self.fps = fps
        self.tile = _noise_tile(spec.seed, 96, 54, spec.ground)
        self._rng = random.Random(spec.seed)
        self._people_colors = [
            self._rng.choice(_PALETTES["person"]) for _ in spec.people
        ]
        self._vehicle_colors = [
            self._rng.choice(_PALETTES["vehicle"]) for _ in spec.vehicles
        ]
        scale = min(W / spec.world_w, H / spec.world_h)
        self.scale = scale
        self.ox = (W - spec.world_w * scale) / 2.0
        self.oy = (H - spec.world_h * scale) / 2.0

    def _to_px(self, x: float, y: float) -> tuple[int, int]:
        return (round(self.ox + x * self.scale), round(self.oy + y * self.scale))

    def _render_ground(self, draw: ImageDraw.ImageDraw) -> None:
        step_x, step_y = W / len(self.tile[0]), H / len(self.tile)
        for i, row in enumerate(self.tile):
            for j, color in enumerate(row):
                draw.rectangle(
                    [j * step_x, i * step_y, (j + 1) * step_x, (i + 1) * step_y],
                    fill=color,
                )

    def _render_roads(self, draw: ImageDraw.ImageDraw) -> None:
        for road in self.spec.roads:
            pts = [self._to_px(x, y) for x, y in road["points"]]
            draw.line(pts, fill=road["color"], width=max(2, round(road["width"] * self.scale)), joint="curve")

    def _entity_at(self, idx: int) -> tuple[list[GTBox], list[dict]]:
        """Draw one frame; returns (ground_truth, drawn_entity_meta)."""
        im = PILImage.new("RGB", (W, H))
        draw = ImageDraw.ImageDraw(im)
        self._render_ground(draw)
        self._render_roads(draw)
        gts: list[GTBox] = []
        meta: list[dict] = []
        t = idx / self.fps
        for i, ent in enumerate(self.spec.people):
            d = ent["speed"] * t
            (x, y), heading = _path_pos(ent["path"], d)
            cx, cy = self._to_px(x, y)
            r = max(2, round(0.8 * self.scale))
            color = self._people_colors[i]
            body_w = round(2.0 * r)
            body_h = round(2.8 * r)
            draw.ellipse([cx - body_w // 2, cy - body_h // 2, cx + body_w // 2, cy + body_h // 2], fill=color)
            draw.ellipse([cx - r, cy - body_h // 2 - r, cx + r, cy - body_h // 2 + r], fill="#2b2b2b")
            draw.ellipse(
                [cx - r // 2, cy + body_h // 3, cx + r // 2, cy + body_h // 3 + r], fill="#2b2b2b"
            )
            gx0 = (cx - body_w // 2) / W
            gy0 = (cy - body_h // 2 - r) / H
            gx1 = (cx + body_w // 2) / W
            gy1 = (cy + body_h // 2 + r) / H
            gts.append(GTBox(class_name="person", bbox=[gx0, gy0, gx1, gy1]))
            meta.append({"type": "person", "world": [x, y], "heading": heading, "class_name": "person"})
        for i, ent in enumerate(self.spec.vehicles):
            d = ent["speed"] * t
            (x, y), heading = _path_pos(ent["path"], d)
            bw, bh = ent["bwh"]
            color = self._vehicle_colors[i]
            rect = self._rotated_rect(x, y, bw, bh, heading)
            px = [self._to_px(px_, py_) for px_, py_ in rect]
            draw.polygon(px, fill=color)
            inner = self._rotated_rect(x, y, bw, bh * 0.55, heading)
            ipx = [self._to_px(px_, py_) for px_, py_ in inner]
            draw.polygon(ipx, fill="#1c1c1c")
            vx = [p[0] for p in px]
            vy = [p[1] for p in px]
            gts.append(
                GTBox(
                    class_name="vehicle",
                    bbox=[min(vx) / W, min(vy) / H, max(vx) / W, max(vy) / H],
                )
            )
            meta.append({"type": "vehicle", "world": [x, y], "heading": heading, "class_name": "vehicle"})
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        keep: list[GTBox] = []
        for gt in gts:
            x0, y0, x1, y1 = gt.bbox
            if x1 <= 0 or y1 <= 0 or x0 >= 1 or y0 >= 1:
                continue  # entirely off-frame: no visible pixels, no ground truth
            gt.bbox = [max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)]
            keep.append(gt)
        return base64.b64encode(buf.getvalue()).decode(), keep, meta

    def _rotated_rect(
        self, cx: float, cy: float, bw: float, bh: float, heading: float
    ) -> list[tuple[float, float]]:
        cos, sin = math.cos(heading), math.sin(heading)
        corners = [
            (-bw / 2, -bh / 2),
            (bw / 2, -bh / 2),
            (bw / 2, bh / 2),
            (-bw / 2, bh / 2),
        ]
        return [
            (cx + cos * dx - sin * dy, cy + sin * dx + cos * dy) for dx, dy in corners
        ]

    def frame(self, idx: int) -> tuple[str, int, int, list[GTBox]]:
        b64, gts, _ = self._entity_at(idx)
        return b64, W, H, gts

    def meta(self, idx: int) -> dict:
        t = idx / self.fps
        entities: list[dict] = []
        for i, ent in enumerate(self.spec.people):
            d = ent["speed"] * t
            (x, y), heading = _path_pos(ent["path"], d)
            entities.append({"id": f"person-{i}", "world": [x, y], "heading": heading})
        for i, ent in enumerate(self.spec.vehicles):
            d = ent["speed"] * t
            (x, y), heading = _path_pos(ent["path"], d)
            entities.append({"id": f"vehicle-{i}", "world": [x, y], "heading": heading})
        return {"source": "synthetic", "scenario": "default", "frame": idx, "world": [self.spec.world_w, self.spec.world_h], "entities": entities}