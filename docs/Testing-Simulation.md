# Testing the Simulation

Author: Victor.I

How to run CV Lab (real YOLO path) and the legacy agent replay with live CV.

## Prerequisites

- Docker and Docker Compose
- Trained weights at `data/models/visdrone-yolov8n.pt` (compose mounts this)
- Optional MP4s under `data/videos/` — build with `python scripts/build_visdrone_mp4s.py`

## Recommended: CV Lab on the real pipeline

```bash
# Load env (MODEL_PATH defaults to /models/visdrone-yolov8n.pt in compose)
[ -f .env ] && set -a && source .env && set +a

./scripts/run_local.sh
# or: docker compose up -d

# Build site/aerial MP4s from VisDrone sequences (once)
python3 scripts/build_visdrone_mp4s.py --max-seqs 8

# Dashboard
cd dashboard && npm run dev
```

Open **Simulation → CV Lab**:

1. Layer 1 — pick an **MP4** (preferred) or VisDrone still sequence
2. Start exercise — frames go to `inference.frames` → YOLO → `inference.detections`
3. Scoreboard + overlays show TP/FP/FN against GT

Agent Replay tab: Play with **Live CV (YOLO)** checked — map on the left, camera CV on the right (uses `site-railway-0000001.mp4` when present).

## Verify inference is not stubbed

```bash
curl -s http://localhost:8005/health
# expect: "model_loaded": true
```

If false, check `data/models/visdrone-yolov8n.pt` exists and compose volume `./data/models:/models:ro`.

## Extended training (simulation consumes better weights)

```bash
./scripts/train_extended.sh
# then point MODEL_PATH at data/models/visdrone-yolov8n-ext.pt
```

Promotion through the eval gate is separate — lab can load weights via MODEL_PATH without promote.

## Legacy Option B (old stub scripts)

The older `simulation/agent_simulator.py` + `sensor_emulator.py` path still exists for telemetry smoke tests. Prefer CV Lab + Agent Replay CV for detection work.

```bash
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
./scripts/run_simulation.sh
```
