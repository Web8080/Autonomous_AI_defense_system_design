"""Layer 2: procedural aerial scenes through the production pipeline.

Author: Victor.I

Composes a top-down drone view (ground, roads/rails, buildings, people, vehicles)
and publishes JPEG frames to `inference.frames`. Ground truth uses VisDrone
class names (`pedestrian`, `car`, `truck`, `van`, `bus`) so scores against the
trained model are honest.

Deterministic given a seed: same scenario + fps → same frames.
"""
from __future__ import annotations

import base64
import io
import math
import random
from dataclasses import dataclass, field

import PIL.Image as PILImage
from PIL import ImageDraw, ImageFilter

from scoring import GTBox

W, H = 960, 540

_PERSON_COLORS = ["#c45c4a", "#d4894a", "#3d8f7a", "#6b5a9e", "#b87a96", "#d4cfc0", "#2a6f97"]
_CAR_COLORS = ["#3a6ea5", "#b83b3b", "#2d5a3d", "#c9a227", "#4a4f55", "#8b5a2b", "#1a1a1a", "#e8e8e8"]
_TRUCK_COLORS = ["#5c6b73", "#8a6d3b", "#3d4a52"]

GROUND_PALETTE = {
    "grass": ["#3a5f29", "#33521f", "#41692d", "#2f4d20", "#446f30", "#375826"],
    "gravel": ["#8b8577", "#7d7668", "#948d7e", "#847d6f", "#77706a", "#6e675f"],
}


def _interp(p: tuple[float, float], q: tuple[float, float], t: float) -> tuple[float, float]:
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _path_pos(points: list[tuple[float, float]], dist: float) -> tuple[tuple[float, float], float]:
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


@dataclass
class SyntheticSpec:
    world_w: float = 240.0
    world_h: float = 140.0
    ground: str = "grass"
    roads: list[dict] = field(default_factory=list)
    rails: list[dict] = field(default_factory=list)
    buildings: list[dict] = field(default_factory=list)
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
        rails=[
            {"points": [(0, 95), (240, 100)], "gauge": 3.2},
            {"points": [(0, 105), (240, 110)], "gauge": 3.2},
        ],
        buildings=[
            {"xy": (20, 15), "wh": (28, 18), "color": "#5a5048"},
            {"xy": (175, 12), "wh": (40, 22), "color": "#4e565c"},
            {"xy": (95, 108), "wh": (35, 20), "color": "#6a5e52"},
            {"xy": (200, 100), "wh": (22, 28), "color": "#3f484e"},
        ],
        people=[
            {"path": [(10, 70), (40, 72), (80, 68)], "speed": 1.2},
            {"path": [(55, 30), (55, 80), (70, 90)], "speed": 1.0},
            {"path": [(150, 20), (150, 55), (160, 70)], "speed": 1.1},
            {"path": [(200, 140), (200, 90), (175, 75)], "speed": 0.9},
            {"path": [(30, 130), (45, 120), (60, 135)], "speed": 0.8},
            {"path": [(110, 120), (130, 90), (140, 110)], "speed": 1.4},
            {"path": [(90, 50), (100, 55), (110, 48)], "speed": 0.7},
            {"path": [(180, 80), (190, 85), (200, 78)], "speed": 1.0},
        ],
        vehicles=[
            {"path": [(0, 70), (240, 85)], "speed": 7.0, "bwh": (4.6, 2.2), "class_name": "car"},
            {"path": [(240, 70), (0, 70)], "speed": 5.0, "bwh": (4.6, 2.2), "class_name": "car"},
            {"path": [(150, -10), (150, 60), (210, 80)], "speed": 4.0, "bwh": (7.2, 2.6), "class_name": "truck"},
            {"path": [(60, 140), (60, 0)], "speed": 5.5, "bwh": (4.2, 2.0), "class_name": "car"},
            {"path": [(20, 95), (220, 102)], "speed": 3.5, "bwh": (8.0, 2.8), "class_name": "van"},
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
        rails=[],
        buildings=[
            {"xy": (15, 15), "wh": (22, 16), "color": "#7a6a55"},
            {"xy": (50, 12), "wh": (18, 14), "color": "#8a7058"},
            {"xy": (140, 18), "wh": (30, 20), "color": "#6b5a48"},
            {"xy": (20, 85), "wh": (25, 18), "color": "#746048"},
            {"xy": (155, 85), "wh": (28, 22), "color": "#5c5040"},
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
            {"path": [(70, 40), (90, 45), (85, 55)], "speed": 0.8},
            {"path": [(110, 90), (100, 70), (120, 65)], "speed": 1.0},
        ],
        vehicles=[
            {"path": [(0, 60), (200, 60)], "speed": 6.0, "bwh": (4.4, 2.0), "class_name": "car"},
            {"path": [(200, 60), (0, 60)], "speed": 5.0, "bwh": (4.4, 2.0), "class_name": "car"},
            {"path": [(100, 120), (100, 0)], "speed": 5.5, "bwh": (4.0, 1.9), "class_name": "car"},
            {"path": [(0, 55), (200, 55)], "speed": 4.0, "bwh": (6.0, 2.4), "class_name": "van"},
        ],
        seed=23,
    ),
    "substation-perimeter": SyntheticSpec(
        world_w=220.0,
        world_h=130.0,
        ground="gravel",
        roads=[
            {"points": [(0, 65), (220, 65)], "width": 8.0, "color": "#4a443c"},
            {"points": [(40, 0), (40, 130)], "width": 5.0, "color": "#3c3731"},
        ],
        rails=[],
        buildings=[
            {"xy": (80, 30), "wh": (50, 40), "color": "#4a5258"},
            {"xy": (150, 35), "wh": (35, 30), "color": "#3d454a"},
            {"xy": (20, 90), "wh": (30, 22), "color": "#5a5048"},
        ],
        people=[
            {"path": [(10, 65), (60, 65), (100, 70)], "speed": 1.0},
            {"path": [(40, 20), (40, 80)], "speed": 0.9},
            {"path": [(160, 100), (120, 90), (90, 100)], "speed": 1.2},
            {"path": [(200, 40), (180, 60), (160, 50)], "speed": 0.8},
        ],
        vehicles=[
            {"path": [(0, 65), (220, 65)], "speed": 5.5, "bwh": (4.5, 2.1), "class_name": "car"},
            {"path": [(220, 65), (0, 65)], "speed": 4.5, "bwh": (5.0, 2.2), "class_name": "van"},
            {"path": [(40, 130), (40, 0)], "speed": 4.0, "bwh": (7.0, 2.5), "class_name": "truck"},
        ],
        seed=41,
    ),
}


def _noise_tile(seed: int, gw: int, gh: int, ground: str) -> list[list[str]]:
    rng = random.Random(seed)
    palette = GROUND_PALETTE[ground]
    return [[rng.choice(palette) for _ in range(gw)] for _ in range(gh)]


class SyntheticSource:
    """Renders scripted world frames; each frame is a fresh composition."""

    def __init__(self, spec: SyntheticSpec = SCENARIOS["railway-yard"], fps: float = 10.0) -> None:
        self.spec = spec
        self.fps = fps
        self.tile = _noise_tile(spec.seed, 128, 72, spec.ground)
        self._rng = random.Random(spec.seed)
        self._people_colors = [self._rng.choice(_PERSON_COLORS) for _ in spec.people]
        self._vehicle_colors = []
        for ent in spec.vehicles:
            cls = ent.get("class_name", "car")
            palette = _TRUCK_COLORS if cls in ("truck", "bus", "van") else _CAR_COLORS
            self._vehicle_colors.append(self._rng.choice(palette))
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
            width = max(2, round(road["width"] * self.scale))
            draw.line(pts, fill=road["color"], width=width, joint="curve")
            # centre dashed line
            draw.line(pts, fill="#c9b896", width=max(1, width // 8))

    def _render_rails(self, draw: ImageDraw.ImageDraw) -> None:
        for rail in self.spec.rails:
            pts = [self._to_px(x, y) for x, y in rail["points"]]
            gauge = max(2, round(rail.get("gauge", 3.0) * self.scale))
            draw.line(pts, fill="#2a2a2a", width=gauge + 2, joint="curve")
            draw.line(pts, fill="#9a9a9a", width=max(1, gauge // 2), joint="curve")

    def _render_buildings(self, draw: ImageDraw.ImageDraw) -> None:
        for b in self.spec.buildings:
            x, y = b["xy"]
            bw, bh = b["wh"]
            x0, y0 = self._to_px(x, y)
            x1, y1 = self._to_px(x + bw, y + bh)
            # soft shadow
            draw.rectangle([x0 + 3, y0 + 3, x1 + 3, y1 + 3], fill="#1a1a1a")
            draw.rectangle([x0, y0, x1, y1], fill=b["color"], outline="#2a2a2a")
            # roof hatch
            mid = (y0 + y1) // 2
            draw.line([(x0, mid), (x1, mid)], fill="#2f2f2f", width=1)

    def _rotated_rect(
        self, cx: float, cy: float, bw: float, bh: float, heading: float
    ) -> list[tuple[float, float]]:
        cos, sin = math.cos(heading), math.sin(heading)
        corners = [(-bw / 2, -bh / 2), (bw / 2, -bh / 2), (bw / 2, bh / 2), (-bw / 2, bh / 2)]
        return [(cx + cos * dx - sin * dy, cy + sin * dx + cos * dy) for dx, dy in corners]

    def _entity_at(self, idx: int) -> tuple[str, list[GTBox], list[dict]]:
        im = PILImage.new("RGB", (W, H))
        draw = ImageDraw.ImageDraw(im)
        self._render_ground(draw)
        self._render_roads(draw)
        self._render_rails(draw)
        self._render_buildings(draw)
        gts: list[GTBox] = []
        meta: list[dict] = []
        t = idx / self.fps

        for i, ent in enumerate(self.spec.people):
            d = ent["speed"] * t
            (x, y), heading = _path_pos(ent["path"], d)
            cx, cy = self._to_px(x, y)
            r = max(2, round(0.75 * self.scale))
            color = self._people_colors[i]
            body_w = round(2.0 * r)
            body_h = round(2.8 * r)
            # shadow
            draw.ellipse(
                [cx - body_w // 2 + 2, cy - body_h // 2 + 2, cx + body_w // 2 + 2, cy + body_h // 2 + 2],
                fill="#1a1a1a",
            )
            draw.ellipse(
                [cx - body_w // 2, cy - body_h // 2, cx + body_w // 2, cy + body_h // 2],
                fill=color,
            )
            draw.ellipse(
                [cx - r, cy - body_h // 2 - r, cx + r, cy - body_h // 2 + r],
                fill="#2b2b2b",
            )
            gx0 = (cx - body_w // 2) / W
            gy0 = (cy - body_h // 2 - r) / H
            gx1 = (cx + body_w // 2) / W
            gy1 = (cy + body_h // 2 + r) / H
            gts.append(GTBox(class_name="pedestrian", bbox=[gx0, gy0, gx1, gy1]))
            meta.append({"type": "pedestrian", "world": [x, y], "heading": heading})

        for i, ent in enumerate(self.spec.vehicles):
            d = ent["speed"] * t
            (x, y), heading = _path_pos(ent["path"], d)
            bw, bh = ent["bwh"]
            class_name = ent.get("class_name", "car")
            color = self._vehicle_colors[i]
            rect = self._rotated_rect(x, y, bw, bh, heading)
            px = [self._to_px(px_, py_) for px_, py_ in rect]
            # shadow offset
            shadow = [(p[0] + 2, p[1] + 2) for p in px]
            draw.polygon(shadow, fill="#1a1a1a")
            draw.polygon(px, fill=color)
            inner = self._rotated_rect(x, y, bw * 0.55, bh * 0.45, heading)
            ipx = [self._to_px(px_, py_) for px_, py_ in inner]
            draw.polygon(ipx, fill="#1c1c1c")
            # windshield hint
            nose = self._rotated_rect(x + math.cos(heading) * bw * 0.15, y + math.sin(heading) * bw * 0.15, bw * 0.25, bh * 0.7, heading)
            npx = [self._to_px(px_, py_) for px_, py_ in nose]
            draw.polygon(npx, fill="#87a0b5")
            vx = [p[0] for p in px]
            vy = [p[1] for p in px]
            gts.append(
                GTBox(
                    class_name=class_name,
                    bbox=[min(vx) / W, min(vy) / H, max(vx) / W, max(vy) / H],
                )
            )
            meta.append({"type": class_name, "world": [x, y], "heading": heading})

        # light aerial haze / soft focus so PIL does not look like flat icons
        im = im.filter(ImageFilter.GaussianBlur(radius=0.4))
        overlay = PILImage.new("RGB", (W, H), "#8899aa")
        im = PILImage.blend(im, overlay, 0.04)

        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=84)
        keep: list[GTBox] = []
        for gt in gts:
            x0, y0, x1, y1 = gt.bbox
            if x1 <= 0 or y1 <= 0 or x0 >= 1 or y0 >= 1:
                continue
            gt.bbox = [max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)]
            keep.append(gt)
        return base64.b64encode(buf.getvalue()).decode(), keep, meta

    def frame(self, idx: int) -> tuple[str, int, int, list[GTBox]]:
        b64, gts, _ = self._entity_at(idx)
        return b64, W, H, gts

    def meta(self, idx: int) -> dict:
        t = idx / self.fps
        entities: list[dict] = []
        for i, ent in enumerate(self.spec.people):
            d = ent["speed"] * t
            (x, y), heading = _path_pos(ent["path"], d)
            entities.append({"id": f"pedestrian-{i}", "world": [x, y], "heading": heading})
        for i, ent in enumerate(self.spec.vehicles):
            d = ent["speed"] * t
            (x, y), heading = _path_pos(ent["path"], d)
            entities.append(
                {
                    "id": f"{ent.get('class_name', 'car')}-{i}",
                    "world": [x, y],
                    "heading": heading,
                }
            )
        return {
            "source": "synthetic",
            "scenario": "default",
            "frame": idx,
            "world": [self.spec.world_w, self.spec.world_h],
            "entities": entities,
        }
