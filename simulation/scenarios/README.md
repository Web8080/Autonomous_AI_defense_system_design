# Scenario-based simulation

Scenarios define an environment (e.g. railway line, perimeter, grid) and initial conditions. The scenario runner executes agent behavior over time and produces a replay that the dashboard can play back with a timeline and overlays.

## Scenario schema

- **name**: Display name.
- **bounds**: `[x_min, y_min, x_max, y_max]` for the 2D view.
- **environment**: Optional type hint: `railway`, `perimeter`, `grid`, `custom`.
- **path** (e.g. railway line): Polyline as list of `[x, y]` points. Assets can patrol along it or respond to points on the path.
- **zones**: List of `{ "id", "name", "polygon": [[x,y],...], "type": "restricted" | "patrol" | "neutral" }`.
- **assets**: Initial agents: `{ "id", "name", "asset_type", "region_id", "start": [x, y], "tasks": [] }`.
- **threats** (optional): Time-staged events: `{ "t_sec", "position": [x,y], "type", "duration_sec" }` for testing agent response.
- **duration_sec**: Total scenario length in seconds.

## Use cases

- **Railway line**: Path = track; assets patrol or respond to incidents along the track; threats at waypoints.
- **Perimeter**: Path = fence line; zones inside/outside; assets patrol boundary.
- **Grid**: Bounds and zones only; assets move between waypoints; test routing and collision avoidance.

Save scenarios as JSON in this folder and run with `python -m simulation.scenario_runner scenarios/your_scenario.json`.
