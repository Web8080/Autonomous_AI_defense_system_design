# Scenario-based visual simulation

You can run scenario-based simulations (e.g. railway line, perimeter) and watch how the AI agent would behave in a live-like environment: agents move along paths, react to threats, and you can play back the run with a timeline and overlays (trails, zones).

## What you get

- **Scenarios**: JSON definition of the environment (path e.g. railway track, zones, assets, optional threats at specific times).
- **Runner**: Script that steps time and records agent positions and decisions into a replay file.
- **Dashboard viewer**: 2D playback with play/pause, timeline scrubber, asset trails, and threat markers. Similar in spirit to a 3D football sim with timeline and overlays, but for critical-infrastructure scenarios.

## Create a scenario

1. Copy or edit a scenario under `simulation/scenarios/`. Example: `railway_line.json`.
2. Define:
   - **bounds**: 2D area for the view.
   - **path**: Polyline (e.g. railway track) as `points: [[x,y], ...]`.
   - **zones**: Optional polygons (patrol, restricted).
   - **assets**: Agents with `start` position and optional `patrol_path`, `speed`.
   - **threats**: Time-staged events at `t_sec` with `position` and `type` (e.g. intrusion, obstacle).
   - **duration_sec**: Length of the run in seconds.

## Run the scenario and generate replay

From the repo root:

```bash
cd simulation
pip install -r requirements.txt
python scenario_runner.py scenarios/railway_line.json
```

This writes `replay/railway_line_replay.json` (or set output via second argument). The replay contains time-series frames: for each time step, asset positions, active threats, and decisions.

## View the simulation in the dashboard

1. **Serve the replay via the API**  
   The dashboard loads replay from `GET /api/v1/simulation/replay?name=railway_line_replay.json`.  
   - Point the gateway at the replay directory: set `REPLAY_DIR` to the full path of the `simulation/replay` folder (e.g. where `railway_line_replay.json` lives). Restart the api-gateway.  
   - Or copy `replay/railway_line_replay.json` into a folder that your backend serves and configure the gateway to serve from that folder (same `REPLAY_DIR` or equivalent).

2. **Open the viewer**  
   In the dashboard, go to **Simulation**. Enter the replay filename (e.g. `railway_line_replay.json`) and click **Load replay**.

3. **Play back**  
   Use **Play** / **Pause** and the timeline slider. Toggle **Player trails** and **Zones** to see movement history and zone outlines. You’ll see agents (e.g. patrol drone, ground responder) moving along the path and threats appearing at the defined times.

## Different use cases

- **Railway line**: Use the provided railway scenario; add more assets or threats in the JSON to test patrol and incident response.
- **Perimeter**: Define a path as the fence line, set zones inside/outside, and assets that patrol the path.
- **Custom grid**: Use bounds and zones only; give assets waypoints or scripted behaviors in the scenario runner to test routing and response.

The runner is currently deterministic (patrol along path, react to threats). To reflect real AI decisions you can later plug in the same logic used by the autonomous agent (e.g. consume detections, emit commands) and record those decisions into the replay so the viewer shows “how the AI agent would handle” the scenario.

## Summary

| Step | Action |
|------|--------|
| 1 | Edit or add a scenario in `simulation/scenarios/*.json` (e.g. railway, perimeter). |
| 2 | Run `python scenario_runner.py scenarios/railway_line.json` to generate a replay. |
| 3 | Set gateway `REPLAY_DIR` to the replay folder and restart the gateway. |
| 4 | In the dashboard, open **Simulation**, load the replay by name, then use Play/Pause and the timeline to watch the run. |

This gives you a video-style simulation of how agents perform in different scenarios (e.g. railway line) with a 2D view, timeline, and overlays.
