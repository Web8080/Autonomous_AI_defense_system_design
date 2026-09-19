"""Curated demo environments for investor / operator presentations.

Author: Victor.I

Each entry maps a human-facing site to a real aerial MP4 under SIM_VIDEO_DIR.
The dashboard plays the file as HTML5 video while the CV Lab exercise
decodes the same clip frame-by-frame through the product pipeline.
"""
from __future__ import annotations

from pathlib import Path

from frames_l1 import VIDEO_DIR, list_videos, resolve_video

# Pitch-ready sites. Order matters — first is the default on the UI.
DEMO_ENVIRONMENTS: list[dict] = [
    {
        "id": "railway-corridor",
        "title": "Railway corridor",
        "subtitle": "Overhead patrol along track and yard",
        "video_id": "site-railway-0000001",
        "fallback_video_ids": ["patrol-overhead-60s"],
        "public_path": "/demo-videos/railway-corridor.mp4",
        "blurb": "Long-form aerial of a rail corridor — vehicles, pedestrians, and infrastructure in one pass.",
    },
    {
        "id": "urban-sprawl",
        "title": "Urban sprawl",
        "subtitle": "Mixed traffic over city blocks",
        "video_id": "site-aerial-0000001",
        "fallback_video_ids": ["site-aerial-0000026"],
        "public_path": "/demo-videos/urban-sprawl.mp4",
        "blurb": "Dense urban FOV — cars, vans, and people under the drone path.",
    },
    {
        "id": "site-perimeter",
        "title": "Site perimeter",
        "subtitle": "Facility edge and approach roads",
        "video_id": "site-aerial-0000069",
        "fallback_video_ids": ["site-aerial-0000026", "site-aerial-0000021"],
        "public_path": "/demo-videos/site-perimeter.mp4",
        "blurb": "Shorter perimeter sweep for a tight live-detection demo.",
    },
]


def resolve_demo_video_id(env: dict) -> str | None:
    """Return the first video id that exists on disk for this environment."""
    candidates = [env["video_id"], *env.get("fallback_video_ids", [])]
    for vid in candidates:
        try:
            resolve_video(vid)
            return vid
        except FileNotFoundError:
            continue
    return None


def list_demo_environments() -> list[dict]:
    """Environments annotated with availability for the layers API."""
    available_ids = {v["id"] for v in list_videos()}
    out: list[dict] = []
    for env in DEMO_ENVIRONMENTS:
        resolved = resolve_demo_video_id(env)
        ready = resolved is not None and (
            resolved in available_ids or Path(VIDEO_DIR).exists()
        )
        out.append(
            {
                **env,
                "video_id": resolved or env["video_id"],
                "available": bool(resolved),
            }
        )
    return out
