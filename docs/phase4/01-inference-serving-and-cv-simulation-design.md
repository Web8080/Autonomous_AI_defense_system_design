# Phase 4 Design Notes — Inference Serving Platform & World-Class CV Simulation

Scope: map the **inference serving** performance layer and the **CV-in-the-loop
simulation** onto this project, referencing the code that exists today, the
phases where each piece lands, and the targets that define "good".

The roadmap compresses a lot into one row ("TensorRT edge deployment, Jetson,
real airframe"). This document unpacks it: the serving layer and the simulator
are where performance engineering and believable exercises actually happen.

---

## 1. How this project maps onto the AI inference stack

```
AI model (YOLOv8n fine-tune, Phase 2 — done)
      │  ONNX export → TensorRT (FP16/INT8)
      ▼
Optimisation / Compilation
      │  engine per device core (CUDA GPU / DLA), INT8 calibration
      ▼
Inference serving platform   ◄── the layer this document engineers
      │                        (latency, throughput, batching, memory,
      │                         concurrency, HW utilisation, profiling)
      ▼
Custom AI hardware  →  Jetson Orin (GPU + DLA) on the aircraft
      ▼
Real-world device   →  camera stream → edge CV → detections → alerts
```

In the product's own vocabulary: the **drone bridge** owns MAVLink/telemetry;
the **inference service owns the serving layer**. Today it loads `MODEL_PATH`
(a `.pt`, CPU/MPS). After Phase 4 it loads a **TensorRT `.engine`** pulled from
the registry by provenance, and the same code path keeps working because the
detection schema (`inference.detections` → `detections.detections`) does not
change. The serving layer is the contract; the runtime underneath is an adapter.

## 2. Inference serving performance — the five dimensions, productised

Product SLOs that make every number below meaningful:

- **camera frame → operator alert < 1.5 s** (real-time patrol),
- **detection → durable row < 250 ms** (the training-data promise — no lost labels).

### 2.1 Latency — "how fast does the model answer?"

Where time goes on the aircraft (budgets; Phase 4 measures on hardware):

| Stage | Budget | Owner |
|---|---|---|
| Camera capture + downscale | 10–30 ms | camera / gstreamer |
| Preprocess (resize/letterbox → NCHW, FP16) | 3–8 ms | serving layer |
| **TensorRT inference** (YOLOv8n @ 640) | 8–15 ms FP16 / 3–8 ms INT8 | serving layer |
| Postprocess (NMS → detections schema) | 5–10 ms | serving layer |
| Package + Kafka publish (local) | 1–3 ms | serving layer |

Per-stage profiling is a standing rule: the model alone can be 10 ms and the
end-to-end alert still hits 2 s because the transmit/queue legs are fat. The
eval gate's `latency_p95_ms ≤ 150` must be measured **end-to-end on the serving
device**, not on the model and not on the MacBook. Phase 2 records the MPS
smoke number and says so in the eval record; Phase 4 re-certifies on Orin.
First profiling pass lives in `bench/`; a nightly Jetson job tracks
p50/p95/p99 per stage so a regression is caught at the stage it happens.

### 2.2 Throughput — "frames per second through the device"

A patrol camera runs 5 fps @ 1280; the aircraft may carry two cameras. Target
for one Orin: **≥ 20 fps @ 1280 FP16, ≥ 30 fps INT8** single stream, with
headroom for a second stream (e.g. thermal). Only **detections + thumbnails**
leave the aircraft (already the design); throughput never means shipping video.

### 2.3 Batching — "can multiple requests ride one kernel launch?"

- HTTP `/infer/batch` already accepts up to 20 frames;  `MAX_DETECTIONS_PER_FRAME`
  caps output at 50 per frame (both in `inference_service/main.py`).
- The win is at the edge: **TensorRT dynamic batch across camera streams** — one
  engine, 4–8 frames per launch — and `NvMultiStream` GPU preemption so one
  stream's burst cannot block another stream's alert.
- Batching is a latency-vs-throughput tunable per stream, never a queue that
  hides frames behind a burst. An alert frame must not sit behind a non-alert
  frame: the serving layer prioritises (the promise of temporal confirmation in
  the alert path makes one dropped frame harmless; a 2 s queue is not).

### 2.4 Memory — "what does the model cost the device?"

- YOLOv8n: fp32 ~6.2 MB → fp16 ~3.1 MB → int8 ~1.6 MB weights. Trivial on an
  Orin; the budget that matters is the **total resident state**: model +
  engine workspace + frame queues + Kafka buffers. Cap and measure, don't
  assume (a Jetson Unified Memory blow-up silently halves throughput).
- Input policy is already enforced (b64 ≤ 10 MB, `MAX_BATCH_FRAMES=20`); the
  threat is growth from concurrency, not per-frame size.

### 2.5 Concurrency — "many devices run simultaneously"

- Kafka partitions per site/asset; **stateless** inference replicas; all
  replicas load the same production artifact resolved from
  `GET /ml/models/production` (already the single source of truth). Add
  replicas to absorb more devices; no coordination state to migrate.
- The registry's promote-gate guarantees one certified artifact per
  deployment; replicas that fail to load it serve `unregistered` stamps and the
  detections consumer refuses those rows — fail-safe, not silent.

### 2.6 Hardware utilisation — "are the GPU and DLA actually busy?"

- TensorRT engine builder targets either the CUDA GPU or the DLA; picking both
  doubles the cost of the wrong choice. Baseline with `tegrastats`/`nvidia-smi`,
  then move kernels only on evidence. INT8 lets the DLA handle the detector
  while the GPU keeps a stream for a second model/trackers.
- Idle-wait is the usual waste: batch into the gaps instead of reacting to them.

### 2.7 Model optimisation — "can it run faster on this hardware?"

- **ONNX export → TensorRT.** FP16 first (no calibration), then INT8 with
  calibration. The calibration set is carved from a **held-out slice of the
  cleaned VisDrone train set** — never the frozen benchmark; the benchmark
  stays sacred for evaluation only.
- **Re-certify after every format change.** A quantised engine is a *new
  candidate*: register it (`runtime=tensorrt`, `precision=int8`), record an
  on-device eval against the frozen benchmark, and let the existing promotion
  gate + regression check decide. FP32 pass is necessary but not sufficient.
  This is how the discipline in Phase 2 becomes the safety for Phase 4: a bad
  INT8 calibration is caught by `false_alarms_per_hour` before it ever flies.
- On the cloud/backfill side: TorchServe/OvMS-style serving only for the
  capture/reprocessing path (the same detections schema), never in the
  real-time loop. Training (Phase 2, MPS) and serving (Phase 4, Orin) are
  deliberately different machines.

---

## 3. World-class simulation — exercising the CV + alert path for real

### 3.1 The gap

Today the CV loop cannot be exercised believably:

- `simulation/sensor_emulator.py` publishes a **stub frame** (black image with a
  red square) — nothing a detector recognises, so no real detection ever flows.
- `simulation/scenario_runner.py` models agents/threats as **2D coordinates**
  over time (replay JSON); it has no imagery at all.
- The SITL world is the stock Gazebo x500 — a bare world with no people,
  vehicles, or infrastructure to detect.

The goal: a simulation that looks like a video-game world, with people in the
environments, so a simulation exercise triggers the real detection→alert chain.

### 3.2 The three-layer path (cheap → true world-class)

**Layer 1 — Real-frame replay through the live pipeline (buildable today).**
The Phase 2 temporal corpus (`data/visdrone-yolo/corpus/corpus.json`) is
sequence-aligned **real aerial footage with real people/vehicles and ground
truth** (76 segments, 548 frames, 5 fps nominal). Adding a `--replay` mode to
`sensor_emulator.py` publishes those frames to `inference.frames` exactly like a
flight: asset_id/frame_id → inference → real detections → `detections.detections`
→ (with the Phase 3 alert layer) alerts. Zero new engines, zero licensing risk
(frames stay local). This is a real end-to-end CV simulation today: "simulated
patrol over a railway yard actually perceives pedestrians and cars."

Layer 1 also makes the **simulation a test**: an exercise = a selected sequence
as a flight; `scenario_runner` asserts that the expected detections/alerts fired
against the sequence ground truth. The sim becomes a regression suite: "do we
still detect people in the yard after the model update?"

**Layer 2 — Synthetic scene composing (Phase 3/4).**
Instead of a full game engine, compose aerial backgrounds + sprite people and
vehicles with motion, occlusion and altitude blur (the repo already handles
image bytes in PIL/numpy). Scripted behaviors (walk the track, stand at the
fence, drive the apron) map straight to **ground-truth boxes per frame**, feed
both the live inference topic and the capture path with
`provenance="sim-synthetic"`. Cheap to move/inject people, and it doubles as a
**synthetic-data engine** that attacks the rare-class imbalance (bus, truck,
awning-tricycle) that the Phase 2 per-class gate is fighting. Night/low-light is
a brightness/noise transform, closing — for simulation purposes — the biggest
honest gap in the Phase 2 eval.

**Layer 3 — Game-engine-grade simulation (Phase 4/5, the gold standard).**
Fully photoreal, scriptable "site digital twin": NVIDIA Isaac Sim / Isaac ROS
ESR (or AirSim + Unreal), USD-authored scenes, pedestrians and vehicles spawned
by script, camera gimbal and lens distortion, lighting presets (dawn / dusk /
night / weather). The SITL drone (PX4 → MAVLink → drone bridge, already wired
in `docker-compose.sitl.yml`) flies the scene; the sim camera publishes frames to
the same `inference.frames` topic, so the **entire real stack** (bridge →
inference → detections → alerts) runs against it. Exercises are recorded,
replayable, and shipped with ground truth. This is the "world-class simulation
clip."

Notes on Layer 3: keep the stock Gazebo world only for dynamics breadboarding;
the visual scene replaces it via the ROS/Omniverse bridge (Isaac ROS can
substitute the Gazebo world while PX4 SITL flight dynamics keep working). The
value is not the renderer, it's that **every simulated pixel flows through the
real serving layer and the real alert path**.

---

## 4. Where each piece lands on the roadmap

| Roadmap row | Work lands |
|---|---|
| Phase 2 (done) | Trained, gated, producible model; temporal corpus; latency smoke bound on MPS; capture path that grows real label data |
| Phase 3 | Layer 1 replay + Layer 2 synthetic scene composing; dashboard live video = replayed/synthetic flights; alert layer (per-class thresholds, temporal N-of-M, zone containment, coalescing); operator triage/active-learning loop feeding corrections back as training data |
| Phase 4 | Layer 3 Isaac Sim; TensorRT FP16/INT8 on Orin; `bench/` per-stage profiling + nightly Jetson latency/throughput job; on-device re-certification through the existing gate; real airframe |
| Phase 5 | Multi-tenant sim farms and per-customer digital twins; elastic serving replicas per site |

The through-line: **the simulator and the serving layer are evaluated by the
same gate.** A simulated flight asserts detections and alerts against ground
truth; a served latency is judged by p95 on the device. Both keep the discipline
Phase 2 established — evidence, fail closed, nothing silently worse.

## 5. Integration points (code, today)

- `backend/services/inference_service/main.py` — swap `MODEL_PATH` for a
  registry-pulled artifact loader (TensorRT adapter); schema unchanged so
  consumers are untouched; keep provenance stamping (`production` /
  `unregistered` / `stub`) working.
- `backend/services/ml_service/main.py` + `eval_gate.py` — register the
  quantised engine as its own model row; eval latency gate gains a
  device-scoped config (`orin-trt-int8`) so an MPS number can never satisfy a
  Jetson SLO.
- `simulation/sensor_emulator.py` — add `--replay <corpus.json>` and
  `--synthetic <scene.yaml>` modes; the frames topic accepts them with no
  schema change.
- `simulation/scenario_runner.py` — add alert/detection assertions + pass/fail
  exercise result, published to `simulation.replay` for the dashboard.
- `backend/ml/build_corpus.py` — the corpus already produced is the Layer 1
  flight library; extend with expected-detection manifests per segment.

## 6. Metrics of record (targets, one place)

| Metric | Phase 2 (now) | Phase 4 (Orin) target |
|---|---|---|
| eval `latency_p95_ms` | MPS smoke bound | ≤ 150 end-to-end @ 1280 |
| single-stream throughput | n/a | ≥ 20 fps FP16 / ≥ 30 fps INT8 |
| camera→alert | not built | < 1.5 s |
| detection→durable row | `< 250 ms` (built path) | same |
| quantisation recall delta | n/a | within gate (≤ per-class floor), re-certified |
| sim exercise | Layer 1 TBD | full scenario suite green nightly |