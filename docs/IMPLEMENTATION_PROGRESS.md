# Implementation progress log

Author: Victor.I

**Rule for agents:** Update this file at the start or end of every turn that changes product state. Append a dated entry; never delete history. Keep "Current status" at the top accurate.

---

## Current status (update every turn)

| Item | State |
|------|--------|
| Date | 2026-09-19 (turn: investor video environments) |
| Stack | Dashboard `:3000`, inference `:8005`, simulation `:8014`, gateway `:8100` |
| Simulation UX | **3 environments** (Railway / Urban / Perimeter) play real MP4 + live YOLO overlays |
| Demo videos | Symlinks under `dashboard/public/demo-videos/` |
| Model | `data/models/visdrone-yolov8s.pt` |
| UI | http://127.0.0.1:3000/dashboard/simulation |
| GitHub | `Web8080/Autonomous_AI_defense_system_design` — commit+push after each major phase |

---

## Implementation history

### 2026-09-19 — Investor demo: real video environments

- Curated environments API (`environments.py`) + layers.environments
- Simulation page: environment cards, HTML5 `<video>` stage, Play video + live detection
- Symlinked MP4s into `dashboard/public/demo-videos/` for browser playback
- Advanced stills / L2 / L3 folded under optional panel

### 2026-09-19 — Localhost refused: restore dashboard + sim + inference

- Diagnosis: nothing listening on 3000/8005/8014; pip opencv into `.venv` hung (3.14 vs 3.13); `setsid` unavailable on macOS
- Run inference + simulation on `.venv-ml` (cv2 5.0.0 + ultralytics); install `kafka-python` there
- Detach with Python `subprocess.Popen(..., start_new_session=True)` so processes survive agent shell exit
- Verified health: inference model_loaded; sim videos=10; dash `/dashboard/simulation` → 200
- L1 POST `video=patrol-overhead-60s` frames=50 → completed, scoring_drain 98%

### 2026-09-19 — Session: sim brush-up + Phase 4–6 software + progress log

#### Simulation / training consumption
- Compose: mount `data/models` → `/models`; default `MODEL_PATH=/models/visdrone-yolov8n.pt`
- Compose: mount full `visdrone-yolo` + `data/videos` for L1
- L1: `CorpusSource` path remap + GT fix; **MP4 `VideoSource`**
- L2: VisDrone class names (`pedestrian`/`car`/…); richer procedural scenes; `substation-perimeter`
- Scoring: class aliases `person`→`pedestrian`, `vehicle`→`car`
- Agent Replay: **Live CV (YOLO)** panel beside map (`agent_replay` → `site-railway-0000001.mp4`)
- Scripts: `build_visdrone_mp4s.py`, `train_extended.sh`, `sim_regression.py`
- Docs: `docs/Testing-Simulation.md` rewritten for CV Lab path

#### Phase 4 software
- `inference_service/runtimes.py` — ultralytics / onnx / tensorrt fail-closed
- Stage latency metrics on `/metrics`
- `bench/profile_stages.py` + `bench/README.md`
- `eval_gate.py` device profiles: `mps-smoke`, `cuda-onnx`, `orin-trt-fp16`, `orin-trt-int8`
- `ml/export_model.py` ONNX; `ml/carve_int8_calib.py`; `scripts/export_tensorrt.sh` (Orin-only)
- L2 Kafka `provenance=sim-synthetic`

#### Phase 5 / 6 scaffold
- `defense_shared/tenancy.py`
- `billing_service` stub (`compose --profile phase5`)
- `docs/phase5/01-phase5-design-notes.md` expanded
- `docs/phase6/` — README, model card, DPIA, AI Act, pen-test templates

#### Tests
- Simulation + gate + runtime + tenancy suites green (41+ related)

#### Explicit non-goals this session
- Roboflow integration (left optional)
- Live Stripe, Orin TRT engines, Isaac twin, real airframe, signed compliance

---

## How to refresh this file (agents)

1. Edit **Current status** table.
2. Append a new `### YYYY-MM-DD — …` section with bullets of what changed.
3. Note blockers and next actions.
4. Author line stays `Victor.I`.

---

## Next actions

1. Open http://127.0.0.1:3000/dashboard/simulation (sign in if prompted).
2. Continue / finish extended YOLOv8s train if still running; re-eval car gate.
3. Optional: add durable launch script so stack survives without agent shells.
4. After each turn: update this file before stopping.
