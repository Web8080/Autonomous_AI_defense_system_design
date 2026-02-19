"""
Run a scenario and produce a replay file for the dashboard viewer.
Loads a scenario JSON (e.g. railway line), steps agent positions and decisions over time,
and writes time-series state to a replay JSON. Optionally publishes to Kafka for live view.
"""
import json
import os
import sys
from pathlib import Path

REPLAY_TOPIC = os.getenv("SIMULATION_REPLAY_TOPIC", "simulation.replay")
OUTPUT_DIR = os.getenv("SIMULATION_REPLAY_DIR", "replay")


def load_scenario(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def project_path(scenario: dict, t: float, path_key: str = "path", speed: float = 5.0) -> list[float]:
    """Return [x, y] along the scenario path at time t (distance = speed * t)."""
    path = scenario.get(path_key) or scenario.get("path", {})
    points = path.get("points", [])
    if not points:
        return [0.0, 0.0]
    total_len = 0.0
    seg_lens = []
    for i in range(1, len(points)):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        seg_lens.append((total_len, (dx * dx + dy * dy) ** 0.5))
        total_len += seg_lens[-1][1]
    if total_len <= 0:
        return list(points[0])
    dist = (speed * t) % total_len
    for start, length in seg_lens:
        if start + length >= dist:
            seg_idx = seg_lens.index((start, length))
            r = (dist - start) / length if length > 0 else 0
            a = points[seg_idx]
            b = points[seg_idx + 1]
            return [a[0] + r * (b[0] - a[0]), a[1] + r * (b[1] - a[1])]
    return list(points[-1])


def run_scenario(scenario_path: str, output_path: str | None = None, dt: float = 1.0) -> str:
    scenario = load_scenario(scenario_path)
    duration = scenario.get("duration_sec", 60)
    assets_cfg = scenario.get("assets", [])
    threats_cfg = scenario.get("threats", [])

    frames = []
    t = 0.0
    while t <= duration:
        frame = {"t": round(t, 2), "assets": [], "threats_active": [], "decisions": []}
        for a in assets_cfg:
            speed = a.get("speed", 5.0)
            pos = project_path(scenario, t, "path", speed)
            frame["assets"].append({
                "id": a["id"],
                "name": a.get("name", a["id"]),
                "asset_type": a.get("asset_type", "drone"),
                "position": pos,
                "status": "in_mission",
                "task": "patrol" if t < duration else "idle",
            })
        for th in threats_cfg:
            start = th["t_sec"]
            end = start + th.get("duration_sec", 10)
            if start <= t < end:
                frame["threats_active"].append({
                    "position": th["position"],
                    "type": th.get("type", "unknown"),
                })
                if not frame["decisions"] and frame["assets"]:
                    frame["decisions"].append({
                        "asset_id": frame["assets"][0]["id"],
                        "intent": "investigate",
                        "target": th["position"],
                    })
        frames.append(frame)
        t += dt

    path_obj = scenario.get("path", {})
    path_points = path_obj.get("points", []) if isinstance(path_obj, dict) else []
    replay = {
        "scenario": scenario.get("name"),
        "bounds": scenario.get("bounds", [0, 0, 1000, 200]),
        "path": path_points,
        "duration_sec": duration,
        "dt": dt,
        "frames": frames,
    }
    out = output_path or os.path.join(OUTPUT_DIR, Path(scenario_path).stem + "_replay.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(replay, f, indent=2)
    return out


def main():
    if len(sys.argv) < 2:
        print("Usage: python scenario_runner.py <scenario.json> [output_replay.json]")
        sys.exit(1)
    scenario_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    out = run_scenario(scenario_path, output_path)
    print("Replay written to", out)


if __name__ == "__main__":
    main()
