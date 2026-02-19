# How to watch the simulation

You already generated a replay with:

```bash
cd simulation
pip install -r requirements.txt
python scenario_runner.py scenarios/railway_line.json
# Replay written to replay/railway_line_replay.json
```

To **watch it live** (playback in the dashboard):

---

## Option A – Dashboard only (no backend)

1. Create the replay folder and copy the file:
   ```bash
   mkdir -p dashboard/public/replay
   cp simulation/replay/railway_line_replay.json dashboard/public/replay/
   ```

2. Start the dashboard:
   ```bash
   cd dashboard
   npm install
   npm run dev
   ```

3. In the browser open **http://localhost:3000**. Log in (any email/password; it will use dev-token when API is localhost, or skip login if you go straight to Simulation).

4. Go to **Simulation** in the nav. Click **Load replay** (default name is `railway_line_replay.json`). The viewer will load it from `/replay/railway_line_replay.json`.

5. Click **Play** to run the simulation. Use the timeline slider to scrub. Toggle **Player trails** and **Zones** as you like.

---

## Option B – With API gateway

1. Start the backend (e.g. `./scripts/run_local.sh` or `docker compose up -d` for gateway and dependencies).

2. Set the gateway env so it can serve the replay file:
   - `REPLAY_DIR=/absolute/path/to/Autonomous_AI_Defense_System_for_Critical_Infrastructure/simulation/replay`
   - Restart the api-gateway.

3. Start the dashboard (`cd dashboard && npm run dev`), open **http://localhost:3000**, log in, go to **Simulation**, load `railway_line_replay.json`, then **Play**.

---

## Summary

| Step | Action |
|------|--------|
| 1 | Replay file is at `simulation/replay/railway_line_replay.json` (you already have it). |
| 2 | Either copy it to `dashboard/public/replay/` (Option A) or set `REPLAY_DIR` on the gateway (Option B). |
| 3 | Run the dashboard, open **Simulation**, load the replay, press **Play** to watch. |

That’s “live” in the sense of watching the simulation play back with a timeline; the scenario runner has already finished and written the replay. For real-time streaming while the scenario runs, we’d need a streaming mode (e.g. runner writing frames to an endpoint or WebSocket); for now, run the scenario, then load and play the replay.
