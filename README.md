# Drone Infrastructure Monitoring

![Banner](.github/social-preview.jpg)

**Autonomous drone patrol and anomaly detection for critical infrastructure: rail corridors, electrical substations, solar and wind farms, ports, reservoirs and industrial perimeters.**

Drones fly scheduled or operator-triggered missions. Onboard computer vision detects intrusions, trespass, vegetation encroachment and equipment faults. Operators get a live map, live video, an alert queue and a full audit trail. **Every dispatch is approved by a human before the aircraft arms.**

> **Live now:** Two-stage VisDrone fine-tune in progress (`21/35` stage-2, mAP@50 0.272 → 0.30 gate), CV Simulation Lab with 3 layers streaming through the real pipeline, 100/100 tests passing. See [Roadmap](#roadmap) and [Design Notes](#design-notes).

---

## Table of Contents
- [What this replaces](#what-this-replaces)
- [Product boundary](#product-boundary)
- [Architecture](#architecture)
- [How Everything Connects — Wireflows](#how-everything-connects--wireflows)
- [Tech Stack — Full Stack · Backend · ML · Data Science · AI · Security](#tech-stack)
- [Simulation Lab — 3 Layers Live on the Real Pipeline](#simulation-lab--3-layers-live-on-the-real-pipeline)
- [ML & Data Science Pipeline](#ml--data-science-pipeline)
- [Security & AI Safeguards](#security--ai-safeguards)
- [Setup](#setup)
- [Testing & Health](#testing--health)
- [Deployment — Microsoft Azure](#deployment--microsoft-azure)
- [Roadmap](#roadmap)
- [Design Notes](#design-notes)
- [Known gaps](#known-gaps)
- [Project Structure](#project-structure)

---

## What this replaces

Sites of this kind are inspected today by walking patrols, manned vehicle rounds and periodic helicopter or rope-access surveys. Those are expensive, infrequent and dangerous. A drone flying a fixed patrol four times a night covers more ground, more often, with a consistent record of what it saw.

## Product boundary

This system **observes and alerts**. It does not take autonomous physical action against anything.

- No lethal, kinetic or irreversible autonomous action, ever.
- No autonomous dispatch without a named human approving it first. The approval is recorded against the flight.
- No real-time biometric identification. Faces and number plates are blurred at the edge by default.

These are product constraints, not defaults to be configured away.

---

## Architecture

**Pattern:** Event-driven microservices — stateless FastAPI services, Kafka (Redpanda) as the event backbone, TimescaleDB + PostGIS for time-series + geospatial, Redis for sessions/rate-limiting. Chosen over monolith/hybrid in [`docs/phase0/03-architectural-exploration.md`](docs/phase0/03-architectural-exploration.md) for independent scaling of inference vs API, clear bounded contexts, and replay for training.

```
Dashboard (Next.js, MapLibre, three.js) ──REST/WS──▶  API Gateway (FastAPI, JWT + RBAC + audit)
                                                         │
         ┌───────────┬───────────┬───────┴───┬───────────┬───────────┬──────────────┐
       Auth       Asset      Mission      Alert     Telemetry    Control    Simulation Lab
       (JWT)   (PostGIS)  (PostGIS)   (Kafka→PG)  (Timescale)  (MAVSDK)   (L1/L2/L3)
                                                         │
                               Redpanda (Kafka API) — telemetry.raw, inference.frames, inference.detections
                                                         │
                             ┌───────────▼───────────┐
                             │   Drone Bridge        │
                             │   MAVLink / MAVSDK    │
                             └───────┬───────┬───────┘
                                     │       │
                         PX4 SITL + Gazebo   Pixhawk + Jetson Orin
                           (dev and CI)      (edge inference, TensorRT)
```

**Services** — each is a Dockerized FastAPI app with its own Dockerfile, health endpoint, and DB/Kafka wiring via `docker-compose.yml`:

| Service | Owns | Scale | Port |
|---------|------|-------|------|
| **Auth** | Users, JWT (HS256 15m + rotating refresh), lockout 5×15m | stateless | 8006 |
| **API Gateway** | JWT verify (sig/iss/aud/exp), RBAC, site scoping, audit, rate-limit, CORS, proxy | 2+ replicas | 8000 |
| **Asset** | Drones/vehicles/sensors, `site_id` (canonical) + deprecated `region_id` | PG | 8001 |
| **Mission** | Missions, waypoints, flights, `approved_by/at` gate, dispatch | PG + drone-bridge | 8007 |
| **Telemetry** | `telemetry.vehicle_state` hypertable (GEOGRAPHY), Kafka consumer `telemetry.raw` → PG | Kafka partitioned | 8002 |
| **Inference** | YOLO (`MODEL_PATH` / registry `production`), `inference.frames` → `inference.detections`, `model_id/version` + `provenance` stamp | stateless, GPU | 8005 |
| **ML Registry** | `ml.models` / `ml.model_evals`, content hash, eval gate, promotion (exactly 1 `production`) | PG | 8011 |
| **Alert** | Threshold (≥0.7 medium/≥0.8 high) + `provenance≠stub`, `alerts` table | Kafka | 8003 |
| **Control** | Commands, e-stop, `audit.command_log`, dispatch to drone-bridge (MAVSDK) | PG + MAVSDK | 8004 |
| **Drone Bridge** | `VehicleLink` + `MAVLinkBridge` + `safety.py` (geofence, pre-arm, watchdog RTL), `telemetry_publisher` → Kafka | per-asset | 8010 |
| **Detections Consumer** | `detections.detections` (training corpus), refuses `stub`/`unregistered` | PG | — |
| **Simulation Lab** | L1/L2/L3 exercises → `inference.frames`, live scoring, lessons | Kafka | 8014 |

Full C4: [`docs/architecture/C4-CONTEXT.md`](docs/architecture/C4-CONTEXT.md) · [`docs/phase0/04-system-architecture.md`](docs/phase0/04-system-architecture.md) · [`docs/phase0/05-module-dependencies.md`](docs/phase0/05-module-dependencies.md)

---

## How Everything Connects — Wireflows

**1. Telemetry wire** `sensors → Kafka telemetry.raw → Drone Bridge → telemetry_publisher → Kafka → telemetry_service/kafka_consumer.py → telemetry.vehicle_state (Timescale hypertable, GEOGRAPHY) → API Gateway → Dashboard Map (MapLibre)`. Backpressure: Kafka partitions, PG upsert on `(asset_id, ts)`.

**2. Inference wire** `camera/frames → gateway /infer or Kafka inference.frames → inference_service (_run_inference → YOLOv8n, bbox normalized [0,1] + metadata.bbox_px + model_version/provenance) → Kafka inference.detections → {detections_service → detections.detections (training labels), alert_service (threshold), dashboard live}`. Every detection carries `model_id/version`; `stub` (threat 0.0) never creates an alert or label.

**3. Alert wire** `inference.detections (Kafka) → alert_service (provenance check defense-in-depth, threat_score ≥0.7) → alert persistance (PG) → gateway → Dashboard alert queue`. Phase 3 adds per-class operating thresholds from the FAPFH sweep, ByteTrack temporal N-of-M, PostGIS zone containment, and coalescing on `(asset, zone, class, track_id)`.

**4. Control wire** `Dashboard → gateway POST /api/v1/control/command | /emergency-stop (JWT+RBAC, audit.log) → control_service → drone_bridge POST /vehicles/{id}/mission|emergency-stop → MAVSDK (arm/takeoff/land/RTL/kill) → PX4 SITL / Pixhawk`. `safety.py` is layer 2 — PX4 params (`apply_fc_safety_params`) are layer 1 and survive a bridge crash. Heartbeat loss → safe hold.

**5. Mission wire** `Dashboard → gateway → mission_service (mission + waypoints + flight status awaiting_approval) → POST /flights/{id}/approve (records approved_by UUID, FK to auth.users) → POST /flights/{id}/start (403 if not approved) → drone_bridge/upload → arm → execute → watchdog (check_in_flight → ABORT → RTL) → mission_service status (in_flight/returning/completed/aborted/failed)`. `flight_id` end-to-end makes the AI-Act decision chain reconstructable.

**6. Simulation wire (the harness that validates the product)** `simulation_service L1/L2/L3 → Kafka inference.frames (same topic as real camera) → inference (trained model) → inference.detections → scoring (IoU≥0.5 same-class greedily, alert ≥0.7) → dashboard CV Lab live (video + GT vs detection overlay, scoreboard, p50/p95, lessons)`. L1 = VisDrone temporal corpus (76×5 fps, real GT), L2 = procedural aerial composer (exact GT, rare-class engine), L3 = browser three.js FPV world projecting 3D bboxes → base64 ingest. All three exercise the **same pipeline** the hardware flies — lessons flow back into thresholds/zones/data. See [`docs/phase4/01-inference-serving-and-cv-simulation-design.md`](docs/phase4/01-inference-serving-and-cv-simulation-design.md).

**7. Training wire** `VisDrone-DET (AISKYEYE) → backend/ml/prepare.py (12→10 classes: drop ignored/other, clamp, report) → data/visdrone-yolo/{train,val} → data/visdrone-yolo/corpus/corpus.json (sequence-aligned) → data_versioning.freeze_benchmark (stem-overlap refusal, content hashes) → manifests/frozen-visdrone.json → train.py (two-stage: freeze backbone 15e → unfrozen 35e, MPS) → data/models/visdrone-yolov8n.pt → eval.py (mAP + per-class recall@0.25 + latency p95 + FAPFH sweep over corpus) → ml-service POST /models → POST /models/{id}/evals (gate computed server-side) → POST /models/{id}/promote (recorded promoted_by, regression check, exactly one production) → GET /models/production → inference_service sync`.

---

## Tech Stack

| Layer | Technologies | Why |
|-------|--------------|-----|
| **Full Stack** | Next.js 16.3.4 / React 19, MapLibre GL + react-map-gl, three.js 0.160 (L3), Zustand + TanStack Query, Recharts, Tailwind, FastAPI + Pydantic, httpx, uvicorn, Docker Compose + Kubernetes (Helm, Terraform placeholder) | SSR dashboard with live map + 3D, Python API with async proxy, infra as code |
| **Backend** | Python 3.11/3.13, FastAPI per service, PostgreSQL 16 + TimescaleDB + PostGIS, Redis, Redpanda (Kafka API), asyncpg, pytest + pytest-asyncio, Docker (11 services) | Time-series telemetry (hypertables), geospatial zones, at-least-once events, stateless scale |
| **ML** | PyTorch 2.14, Ultralytics 8.4 (YOLOv8n), ONNX → TensorRT (Phase 4, FP16/INT8, per-device engine), `MODEL_PATH` + registry sync, provenance stamping | Edge inference SLO: camera→alert <1.5 s, detection→row <250 ms, ≥20 fps @1280 FP16 / ≥30 fps INT8 on Orin |
| **Data Science** | VisDrone-DET, PIL/numpy, `prepare.py` (bounds clamp, class report), `build_corpus.py` (pseudo-flights), `data_versioning.py` (content hash + stem-overlap quarantine), `eval.py` (mAP, per-class recall, FAPFH sweep @FAR≤2/hr, latency p95) | Evidence, not vibes — every number is measured against the frozen benchmark + temporal corpus |
| **AI (product)** | YOLO detection (learned) + **deterministic** ByteTrack (tracking) + PostGIS containment (zones) + alert coalescing; future: drift (F1/FAR/latency), active-learning loop (`review_state`) | Tracking/zones are explainable: an operator and regulator can be told exactly why an alert fired |
| **Security** | JWT HS256 (15m) + rotating revocable refresh (hashed), RBAC (super_admin/local_operator), site scoping from signed claims, rate-limit + security headers + audit log, OWASP Top 10, AI safeguardrails (intent allowlist, depth/size caps, SSRF allowlist, inference caps) | Zero-trust at the gateway: every route verifies, every dispatch is audited. See below. |

---

## Simulation Lab — 3 Layers Live on the Real Pipeline

The gap: `sensor_emulator.py` was a red square, `scenario_runner.py` was 2D coordinates, Gazebo was an empty x500 world — no real detection ever flowed.

**L1 — Real-footage replay** `data/visdrone-yolo/corpus/corpus.json` (76 segments, 548 frames, 5 fps, real people/vehicles + GT) replays via `sensor_emulator --replay` into `inference.frames`. Zero new engines, zero licensing risk (frames stay local). *Also a regression suite*: a sequence is a flight; the runner asserts expected detections vs GT.

**L2 — Synthetic compose** Procedural aerial world (`frames_l2.py`: grass/gravel noise, roads, sprite people/vehicles with motion + heading + shadow, exact GT clamped to [0,1]) at `scenarios: railway-yard, market-square`. Doubles as a **synthetic-data engine** for rare classes (bus/truck/awning-tricycle/bicycle) and night (brightness/noise transform).

**L3 — Browser 3D ingest** Photoreal(ish) digital twin in the dashboard: `three.js` ground + roads + buildings + fog, 6 people + 3 vehicles patrolling, drone camera (Perspective 58°, 38 m alt, 12 m/s) projecting 3D bboxes → `project(camera)` → normalized xyxy GT, `canvas.toDataURL('jpeg')` → `POST /api/v1/simulation/exercises/{id}/ingest` (gateway → `simulation_service` → Kafka). The SITL drone (PX4) can fly the same scene later.

**Dashboard** `Simulation → CV Lab` — layer pills, sequence/scenario/fps/frames, Start/Stop, video with GT (dashed #58a6ff) vs detection (green TP ✓ / red FP) overlay, live scoreboard (precision/recall/F1, TP/FP/FN/GT, per-class table), latency p50/p95/scoring drain, **lessons → product** (FP bias → tighten threshold/zone, recall gap → grow data, alert pressure). Legacy 2D replay stays under *Agent Replay* tab. Code: `backend/services/simulation_service/` (engine + scoring + transports) + `dashboard/src/components/simulation/BrowserWorld.tsx` + `dashboard/src/app/dashboard/simulation/page.tsx`.

---

## ML & Data Science Pipeline

* **Dataset decision:** VisDrone-DET — drone-captured, dense, tiny objects (5–40 px), matching the patrol envelope better than COCO. Raw 12 classes → effective 10 (pedestrian, people, bicycle, car, van, truck, tricycle, awning-tricycle, bus, motor) after dropping `ignored regions` + `other`. Pinned in `backend/ml/config/dataset.yaml`.
* **Feature engineering that matters:** two-stage transfer (freeze backbone 0–9 → head learns aerial scale, then low-LR full tune; BN re-adaptation), honest label math (clamp + drop zero-area, report car 14k vs bus 251 val → tiered gate floor), temporal product metric (FAPFH @5 fps, not mAP).
* **Quarantine:** `freeze_benchmark` refuses on stem overlap (renames don't hide leaks), `train.py` re-checks, `manifests/frozen-visdrone.json` carries `dataset_hash` `66948…`, `benchmark_hash` `cc9ae5…`, `source_hashes` + `benchmark_name` `visdrone-detect-frozen-2026-09`.
* **Training:** `.venv-ml` (Python 3.13, torch 2.14 MPS, ultralytics 8.4). Stage 1 `15e freeze10 lr0 0.02` → **mAP@50 0.202** / P 0.313 R 0.24 ; Stage 2 `35e freeze0 lr0 0.002` → **21/35 mAP@50 0.272** (see `data/train-s2.log`, `runs/detect/runs/visdrone-s2/`) climbing to the calibrated gate **0.30** (literature YOLOv8n 0.28–0.32) with per-class floor 0.60 common / 0.40 rare (`gt_count<1500`). `deterministic=False` on MPS, seed 42.
* **Eval:** `backend/ml/eval.py --manifest manifests/frozen-visdrone.json --weights data/models/visdrone-yolov8n.pt --corpus-manifest data/visdrone-yolo/corpus/corpus.json --device mps` emits `map_50, map_50_95, precision/recall@conf, recall_at_target_far (FAR≤2/hr sweep), false_alarms_per_hour, latency p95, per_class {recall, gt_count, conf}, per_slice`. Without corpus → null → gate `REJECT` (fail-closed). Product metric sweep: confidence thresholds 0.0→1.0 (401 steps), FP = prediction ≥thresh not IoU-matched, greedy same-class IoU≥0.5.
* **Serving:** `inference_service` resolves `GET /ml/models/production` (single source of truth), stamps every detection, `MAX_IMAGE_B64 10MB`, `MAX_BATCH_FRAMES 20`; TensorRT adapter in Phase 4 (ONNX→engine, per-device, re-certified through the gate — a bad INT8 calibration fails `false_alarms_per_hour` before it flies). SLOs: camera→alert <1.5 s, detection→row <250 ms, ≥20 fps FP16 / ≥30 fps INT8 @1280.

---

## Security & AI Safeguards

**AuthN:** HS256 JWT (sig/iss/aud/exp, 15m), refresh rotation (hashed, revocable), lockout after 5 failures/15m, `gen_jwt_dev.py` for dev tokens. No self-signup — `scripts/seed_admin.py` upserts. **AuthZ:** `require_role` at gateway, `region_ids`/`site_ids` from verified claims only (never client headers). Every privileged action writes `audit_log` with actor + timestamp.

**OWASP:** [Top 10](docs/security/OWASP-TOP10.md) — injection (asyncpg params), XSS (CORS whitelist, SecurityHeadersMiddleware), IDOR (site scoping), SSRF (`is_url_safe_for_fetch` allowlist), rate-limiting (login + simulation), secrets (`.env` + secret manager, never in code/logs).

**AI safeguardrails:** [AI-SAFEGUARDRAILS](docs/security/AI-SAFEGUARDRAILS.md) — `is_allowed_intent` allowlist, `validate_command_payload` depth/size caps, `MAX_IMAGE_B64_BYTES`, `MAX_DETECTIONS_PER_FRAME`, `MAX_BATCH_FRAMES`, `MAX_EXERCISES 8` + `L3_CAP 4096`, `filter_query_params` (ALLOWED_QUERY_*). `stub`/`unregistered` detections are rejected by both producer and consumers — defense in depth.

**Compliance** (engineering, not paperwork): **EU AI Act** high-risk from 2 Aug 2026 — registry + gate + versioning + audit satisfy risk/data/logging/oversight; **UK GDPR** DPIA + edge redaction; **UK CAA** Operator/Flyer ID, 1 Jan 2026 Remote ID, BVLOS via SORA (3–6 mo). See `docs/security/`.

---

## Setup

1. **Prerequisites:** Docker + Compose, Python 3.11 (services) / 3.13 (ML), Node 20+.
2. **Env:** `cp .env.example .env` → set `JWT_SECRET` (32+ chars). Never commit `.env`.
3. **Stack:** `docker compose up -d` (image `timescale/timescaledb-ha:pg16` — PostGIS + Timescale). Or `./scripts/run_local.sh` (boots asset, telemetry, alert, control, mission, inference, ml, detections-consumer, auth, gateway, drone-bridge, simulation).
4. **Admin:** `python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt && DATABASE_URL=postgresql://defense:defense@127.0.0.1:5432/defense .venv/bin/python scripts/seed_admin.py --email you@example.com --password '<12+ chars>'` (upserts, resets lockout).
5. **VisDrone (once):** download `VisDrone2019-DET-{train,val}.zip` (ultralytics release v1.0), unzip to `data/visdrone-raw/`, then:
   ```bash
   .venv-ml/bin/python backend/ml/prepare.py --raw-root data/visdrone-raw --out data/visdrone-yolo
   .venv-ml/bin/python backend/ml/build_corpus.py --val-root data/visdrone-yolo/val --out data/visdrone-yolo/corpus/corpus.json
   PYTHONPATH=backend/services:backend/services/ml_service .venv-ml/bin/python -m ml.data_versioning freeze_benchmark --train-root data/visdrone-yolo/train --benchmark-root data/visdrone-yolo/val --out manifests/frozen-visdrone.json
   ```
   Raw `data/` is gitignored (research-only license); `manifests/` carries hashes.
6. **Train (M1 Pro example):**
   ```bash
   nohup env PYTHONPATH=backend/services:backend/services/ml_service .venv-ml/bin/python backend/ml/train.py --dataset-root data/visdrone-yolo/train --benchmark-root data/visdrone-yolo/val --artifact-out data/models/visdrone-s1.pt --model yolov8n.pt --freeze 10 --lr0 0.02 --epochs 15 --batch 8 --imgsz 640 --device mps --project runs --run-name visdrone-s1 --git-sha $(git rev-parse HEAD) > data/train-s1.log 2>&1 &
   nohup env PYTHONPATH=backend/services:backend/services/ml_service .venv-ml/bin/python backend/ml/train.py --dataset-root data/visdrone-yolo/train --benchmark-root data/visdrone-yolo/val --artifact-out data/models/visdrone-yolov8n.pt --weights data/models/visdrone-s1.pt --freeze 0 --lr0 0.002 --epochs 35 --batch 8 --imgsz 640 --device mps --project runs --run-name visdrone-s2 --git-sha $(git rev-parse HEAD) > data/train-s2.log 2>&1 &
   ```
7. **Eval + promote:**
   ```bash
   .venv-ml/bin/python backend/ml/eval.py --manifest manifests/frozen-visdrone.json --weights data/models/visdrone-yolov8n.pt --corpus-manifest data/visdrone-yolo/corpus/corpus.json --device mps --out /tmp/eval-s2.json
   # register, record eval, promote via gateway (super_admin + promoted_by UUID):
   # POST /api/v1/models, POST /api/v1/models/{id}/evals, POST /api/v1/models/{id}/promote
   ```
8. **Dashboard:** `cd dashboard && npm install && npm run dev` → http://localhost:3000.
9. **Health:** `./scripts/verify_health.sh` ; SITL: `./scripts/run_sitl.sh` (PX4+Gazebo x500, first build 10–20m).

---

## Testing & Health

* **Backend:** `cd backend && pytest tests -q` — **100/100** (incl. 14 simulation: scoring, L2 synthetic determinism + clamp, engine pump + live score, L3 ingest, route creation). Gate 17/17, simulation 14/14. CI builds 11 Docker images (`simulation_service` added) and runs the full suite against `timescale/timescaledb-ha:pg16`.
* **Dashboard:** `npm run typecheck` / `npm run build` — Next 16.3, React 19, MapLibre, three.js all type-clean, static pages 10/10.
* **Simulation smoke:** start stack + `simulation_service` (`:8014`), start an L2 exercise from the dashboard (or `POST /api/v1/simulation/exercises {"layer":2,"scenario":"railway-yard","fps":8,"frames":80}`), poll `GET /api/v1/simulation/exercises/{id}` — watch `recent_frames` JPEGs, `recent_results` detections, `scoreboard` + `lessons`, `latency_ms.p95`, and Kafka topics `inference.frames`/`inference.detections` with `kcat`.
* **Alerts E2E (once model promoted):** L1 replay over the corpus → real `person`/`car` detections → `detections.detections` rows → `alerts` for `threat≥0.7` → dashboard Alert queue.

---

## Deployment — Microsoft Azure

**Target:** production on **Microsoft Azure** — AKS for compute, managed Postgres + Redis + Event Hubs (Kafka) for state, Blob Storage for artifacts, Key Vault for secrets, ACR for images, Front Door + Entra ID for edge. Local `docker compose` is the dev mirror of the Azure topology below.

### Azure mapping (local → Azure)

| Concern | Local (`docker compose`) | Azure | Notes |
|---------|--------------------------|-------|-------|
| **Compute** | `docker compose up` (11 services) | **AKS** (Azure Kubernetes Service) — 1 node pool system (2–3× DS2v2), 1 user pool GPU (NC-series for inference if cloud backfill) + `infra/kubernetes/base/` + Helm `infra/helm/defense` | Horizontal Pod Autoscaler on `inference-service` (CPU + `inference_requests_total`), `api-gateway` 2 replicas, `simulation-service` spot |
| **Registry** | local build | **ACR** (Azure Container Registry) — `az acr build` / `docker push` → AKS `imagePullSecrets` | CI `docker-build` matrix pushes `dis/{service}:${sha}` to ACR |
| **Database** | `timescale/timescaledb-ha:pg16` (PG 16 + Timescale + PostGIS) | **Azure Database for PostgreSQL Flexible Server** (PG 16, `postgis` + `timescaledb` extensions) or **TimescaleDB on AKS** with `Azure Disk` PVC for `pgdata` | Schema `backend/db/schema/*.sql` — enable `CREATE EXTENSION postgis; CREATE EXTENSION timescaledb;` + `SELECT create_hypertable('telemetry.vehicle_state','ts')` |
| **Cache / Rate-limit** | `redis:7` | **Azure Cache for Redis** (Basic → Standard) | `REDIS_URL=rediss://…:6380` |
| **Event bus** | `redpanda` (Kafka API) | **Azure Event Hubs — Kafka surface** (`KAFKA_BOOTSTRAP_SERVERS=…eventhubs.windows.net:9093`) or **Redpanda on AKS** for full Kafka semantics | Topics: `telemetry.raw`, `inference.frames` (100p), `inference.detections`, `telemetry.aggregated`. Use `EH_KAFKA` SASL/SSL |
| **Object store** | local `data/` + `manifests/` | **Azure Blob Storage** (`defense-models`, `defense-datasets`, `defense-telemetry`) — `S3_BUCKET_*` envs map to `AZURE_STORAGE_CONNECTION_STRING` | Artifact URI `az://defense-models/visdrone-yolov8n.pt#sha` → `MODEL_PATH` init container pulls via `azcopy` or S3 compat API |
| **Secrets** | `.env` | **Azure Key Vault** → `external-secrets` operator → K8s `Secret` (`JWT_SECRET` 32+ chars, `DATABASE_URL`, `KAFKA_*`, `ACR` creds) | Never bake secrets; `infra/terraform/variables.tf` marks them `sensitive` |
| **Edge** | Pixhawk + Jetson Orin (TensorRT) | **Jetson Orin** at site, **Azure IoT Hub / Device Provisioning** for fleet, **Arc-enabled Kubernetes** optional for edge AKS | Model artifact resolved via `GET /ml/models/production` → Orin pulls `.engine` (INT8) and re-certifies through the gate |
| **Dashboard** | `next dev :3000` / `next build` | **Azure Static Web Apps** (SWA) or **AKS NGINX Ingress** hosting Next.js — `NEXT_PUBLIC_API_URL=https://api.<env>.azurefd.net` | SWA: `swa deploy` from `dashboard/`; AKS: Helm `ingress` with cert-manager |
| **Ingress / Edge** | `CORS_ORIGINS=http://localhost:3000` | **Azure Front Door + WAF** → **NGINX Ingress** → `api-gateway:8000` | `CORS_ORIGINS=https://dashboard.<env>.azurefd.net`, TLS via Key Vault + `cert-manager` |
| **Identity** | `auth-service` HS256 + `scripts/seed_admin.py` | **Microsoft Entra ID (Azure AD)** federated to `auth-service` (JWT iss `https://login.microsoftonline.com/...`) + `auth-service` as fallback | `JWT_ISSUER`/`JWT_AUDIENCE` → Entra `iss`/`aud`; `site_ids` from `appRoles` |
| **Observability** | `stdout` + `scripts/verify_health.sh` | **Azure Monitor + Log Analytics + Managed Prometheus/Grafana** — scrape `GET /metrics` (inference p95, `inference_requests_total`), alerts `latency_p95 >150ms` | `bench/` nightly Jetson job + `monitoring/drift_cron.sh` → Log Analytics |
| **IaC** | `infra/` Helm + K8s + Terraform placeholder | **Terraform `azurerm`** (`infra/terraform/main.tf` → `azurerm_kubernetes_cluster`, `azurerm_postgresql_flexible_server`, `azurerm_redis_cache`, `azurerm_eventhub_namespace`, `azurerm_storage_account`, `azurerm_key_vault`) + **GitHub → ACR → AKS** (`AZURE_CREDENTIALS` secret) | `terraform apply -var-file=prod.tfvars` provisions VNet → AKS → DB → Redis → Event Hubs → Storage → Key Vault |

### Azure topology (text)

```
Entra ID ──OIDC──▶ API Gateway (AKS, 2×, Front Door + WAF)
                          │
        ┌─────────────────┼─────────────────┐
     ACR (images)   Key Vault (secrets)  Blob (models/datasets)
        │                 │                 │
        └────────┬────────┴────────┬────────┘
                 v                 v
            AKS — defence namespace
              ├─ asset / telemetry / alert / control / mission / ml / auth / drone-bridge / simulation / detections-consumer
              ├─ inference-service (HPA, pulls production model from ml-service → Blob)
              ├─ Redpanda or Event Hubs (Kafka) — telemetry.raw / inference.frames / inference.detections
              ├─ PG Flexible Server (Timescale hypertables + PostGIS) + Redis (Cache)
              └─ NGINX Ingress ← Front Door (dashboard SWA → api.<env>.azurefd.net)
                                                   │
Jetson Orin (site) ──IoT Hub──▶ AKS inference (cloud backfill) + edge TensorRT (FP16/INT8, 20/30 fps @1280)
```

### Deploy to Azure — runbook

```bash
# 0. One-time Azure
az login
az group create -n rg-defense-prod -l uksouth
az acr create -n acrdefenseprod -g rg-defense-prod --sku Standard
az aks create -n aks-defense-prod -g rg-defense-prod --attach-acr acrdefenseprod --network-plugin azure --generate-ssh-keys

# 1. Terraform (infra/terraform/)
cd infra/terraform
terraform init
terraform apply -var-file=prod.tfvars  # provisions PG Flexible (postgis+timescale), Redis, Event Hubs (Kafka), Storage, Key Vault

# 2. Secrets → Key Vault → external-secrets
az keyvault secret set --vault-name kv-defense-prod --name JWT-SECRET --value "$(openssl rand -base64 32)"
az keyvault secret set --vault-name kv-defense-prod --name DATABASE-URL --value "postgresql://defense:...@pg-defense-prod.postgres.database.azure.com:5432/defense"

# 3. Build & push (CI does this on push to main)
cd backend && docker build -f services/api_gateway/Dockerfile -t acrdefenseprod.azurecr.io/dis/api_gateway:$(git rev-parse --short HEAD) . && docker push acrdefenseprod.azurecr.io/dis/api_gateway:$(git rev-parse --short HEAD)
# repeat for 11 services OR: gh workflow .github/workflows/ci.yml (docker-build matrix) → ACR

# 4. Helm
helm upgrade --install defense infra/helm/defense -n defence --create-namespace \
  --set image.repository=acrdefenseprod.azurecr.io \
  --set image.tag=$(git rev-parse --short HEAD) \
  --set ingress.host=api.prod.azurefd.net \
  --set secrets.keyVault=kv-defense-prod

# 5. Dashboard (SWA)
cd dashboard
az staticwebapp create -n swa-defense-prod -g rg-defense-prod --source . --branch main --app-location . --output-location .next
# or AKS: helm upgrade --install dashboard infra/helm/defense-dashboard --set env.NEXT_PUBLIC_API_URL=https://api.prod.azurefd.net

# 6. Verify
./scripts/verify_health.sh  # hits https://api.prod.azurefd.net/health + /api/v1/assets + /metrics
```

*Local stays the same:* `docker compose up -d` is the `Azurite + DB + Redpanda` mirror. The only config delta is `KAFKA_BOOTSTRAP_SERVERS` (`redpanda:29092` → `…eventhubs.windows.net:9093` with SASL), `DATABASE_URL` (local PG → Flexible Server), `REDIS_URL` (`redis://…` → `rediss://…:6380`), and `MODEL_PATH` (local `data/models/…` → `az://…` pull). No code change.

See: [`infra/helm/defense/values.yaml`](infra/helm/defense/values.yaml) · [`infra/kubernetes/base/`](infra/kubernetes/base/) · [`infra/terraform/main.tf`](infra/terraform/main.tf) · [`docs/Dashboard-Deployment.md`](docs/Dashboard-Deployment.md)

---

## Roadmap

| Phase | Scope | Status | Design Notes |
|-------|-------|--------|--------------|
| 0 | Tenancy schema, real auth, TimescaleDB/PostGIS, Redpanda | **Done** | [Functional](docs/phase0/01-functional-requirements.md) · [NFR](docs/phase0/02-non-functional-requirements.md) · [Arch Exploration](docs/phase0/03-architectural-exploration.md) · [System Arch](docs/phase0/04-system-architecture.md) · [Module Deps](docs/phase0/05-module-dependencies.md) · [Env & Secrets](docs/phase0/06-environment-and-secrets.md) · [AI Flows](docs/phase0/07-ai-decision-flows.md) |
| 1 | Drone Bridge (MAVSDK), mission + flight API, PX4 SITL, dispatch with human approval gate | **Done** — 56 tests, `VehicleLink`/`MAVLinkBridge`/`safety.py`, SITL `x500` | [Phase 1](docs/phase1/01-phase1-design-notes.md) |
| 2 | VisDrone training pipeline, evaluation gate, model registry — dataset + corpus + two-stage fine-tune, gate + promotion | **In progress** — stage1 done (mAP@50 0.202), stage2 `21/35` (≈0.272 → 0.30 gate), corpus 76×5fps, registry live | [Phase 2](docs/phase2/01-phase2-design-notes.md) |
| 3 | Dashboard rebuild: map, live video, mission planner, triage + CV Simulation Lab (L1/L2/L3) on the real pipeline | **Done (CV Lab)** — L1 replay, L2 synthetic, L3 browser 3D ingest via `simulation_service`; lessons → product; legacy agent replay preserved | [Phase 3](docs/phase3/01-phase3-design-notes.md) · [Testing & Sim](docs/Testing-Simulation.md) · [Scenario Vis](docs/Scenario-Visual-Simulation.md) · [Watch Sim](docs/Watch-Simulation.md) |
| 4 | TensorRT edge deployment, Jetson Orin, real airframe — serving SLOs, INT8 re-certification, Isaac Sim digital twin | **In progress (design + harness)** — SLOs camera→alert <1.5s / detection→row <250ms / ≥20 fps @1280; L1/L2 unblock validation | [Phase 4](docs/phase4/01-inference-serving-and-cv-simulation-design.md) |
| 5 | Multi-tenant SaaS, billing, onboarding | — | [Phase 5](docs/phase5/01-phase5-design-notes.md) — Phase 4 harness + sizing inform per-tenant isolation |
| 6 | DPIA, AI Act technical file, model cards, pen test | — | *Planned — registry + audit trail are the evidence* |

> Full architecture C4: [`docs/architecture/C4-CONTEXT.md`](docs/architecture/C4-CONTEXT.md)

---

## Design Notes

| Doc | What it decides |
|-----|-----------------|
| [Phase 0 — Functional Requirements](docs/phase0/01-functional-requirements.md) | Roles (Super Admin / Local Operator / System AI), 36 Must/Should/Could requirements |
| [Phase 0 — NFR](docs/phase0/02-non-functional-requirements.md) | Latency (infer p95 <200 ms, alert <2 s, e-stop <1 s), 10k telemetry/s, 99.99% e-stop |
| [Phase 0 — Architectural Exploration](docs/phase0/03-architectural-exploration.md) | Monolith vs event-driven vs hybrid — why microservices won (with liability points) |
| [Phase 0 — System Architecture](docs/phase0/04-system-architecture.md) | Containers, control flow, pipeline, dependencies |
| [Phase 0 — Module Dependencies](docs/phase0/05-module-dependencies.md) | Dependency graph + stack per module + shared infra (PG/Kafka/Redis/S3) |
| [Phase 0 — Env & Secrets](docs/phase0/06-environment-and-secrets.md) | Required/optional env, per-service files, hardware-credential policy |
| [Phase 0 — AI Decision Flows](docs/phase0/07-ai-decision-flows.md) | Ingest→infer→alert→decide→execute→override + fail-safe flow |
| [Phase 1 — Drone Bridge & Mission](docs/phase1/01-phase1-design-notes.md) | `drone_bridge` layering (connection/mavlink/safety/mission/telemetry), dispatch data flow, approval gate, SITL, 23 new tests |
| [Phase 2 — Registry, Gate & Training](docs/phase2/01-phase2-design-notes.md) | VisDrone decision, `ml_service` registry + `eval_gate.py` (9 criteria, fail-closed, regression), `data_versioning.py`, `train.py`/`eval.py`, **§§6–8: CV smartness (6 choices), alert gap, 14 failure modes** |
| [Phase 3 — Dashboard & CV Lab](docs/phase3/01-phase3-design-notes.md) | `simulation_service` L1/L2/L3, `BrowserWorld.tsx` three.js FPV, `page.tsx` CV Lab + overlay + scoreboard + lessons, tests 14/14 |
| [Phase 4 — Serving & World-Class Sim](docs/phase4/01-inference-serving-and-cv-simulation-design.md) | Serving 7 dimensions (latency/batching/concurrency/HW/util), 3-layer simulation path (L1 corpus, L2 synthetic, L3 Isaac/AirSim), where each piece lands on the roadmap, SLOs 20/30 fps, <1.5 s |
| [Phase 5 — Multi-Tenant SaaS](docs/phase5/01-phase5-design-notes.md) | Tenancy (`org_id`/`site_id` RLS), onboarding, Stripe billing, per-site model pinning — sizing follows Phase 4 harness |
| [Security — OWASP Top 10](docs/security/OWASP-TOP10.md) | Mapped control per OWASP item |
| [Security — AI Safeguardrails](docs/security/AI-SAFEGUARDRAILS.md) | Allowlist, caps, provenance, SSRF |
| [Testing — Simulation](docs/Testing-Simulation.md) | Full-stack + sim runbook |
| [Simulation — Scenario Visual](docs/Scenario-Visual-Simulation.md) | JSON scenarios + runner + replay viewer |
| [Simulation — Watch](docs/Watch-Simulation.md) | Dashboard-only vs gateway replay |

Phase 3 (dashboard + CV Lab) and Phase 5 (multi-tenancy) each have dedicated design notes above; implementation lives in `dashboard/src/app/dashboard/simulation/page.tsx`, `dashboard/src/components/simulation/BrowserWorld.tsx`, and `backend/services/simulation_service/`.

---

## Known gaps

Tracked honestly, because the repo previously overstated its own completeness.

- **VisDrone model — training in progress.** Stage 1 (freeze backbone, 15 epochs, lr 0.02) finished: mAP@50 0.202 on the frozen 548-image benchmark (38,759 instances). Stage 2 (unfrozen, 35 epochs, lr 0.002) is at epoch 21/35, mAP@50 ≈0.272 and climbing toward the 0.30 gate — see `data/train-s2.log` and `runs/detect/runs/visdrone-s2/`. The model registry, evaluation gate and benchmark quarantine (`manifests/frozen-visdrone.json`, temporal corpus `data/visdrone-yolo/corpus/corpus.json` — 76 pseudo-flights, 5 fps) are live. Until promotion, inference still returns `stub` detections (threat 0.0, never an alert) or `unregistered` if a checkpoint is loaded without a registry entry. (Phase 2 — see `docs/phase2/01-phase2-design-notes.md` §§6–8 for CV-smartness, alert gap and 14 failure modes.)
- Evaluation harness + promotion gate: implemented and operational. The gate fails closed on any missing product metric, and FAPFH/recall-at-target-FAR are only measurable against the temporal flight corpus — `backend/ml/eval.py` sweeps confidence over the corpus to pick the operating point at FAR ≤2/hr. The gate is now calibrated to VisDrone literature (mAP 0.30, tiered per-class floor by `gt_count`) and records `gt_count` per class in the eval.
- **Simulation lab — live on the real pipeline.** `simulation_service` (L1 real-footage replay, L2 synthetic compose, L3 browser 3D ingest) publishes to the same `inference.frames` Kafka topic a physical camera does, so the trained model, detection persistence and alert service treat the feed as production. The dashboard Simulation page now has a **CV Lab** with a layer selector (1/2/3) — video with GT (dashed blue) vs detection (green TP / red FP) overlay, live scoreboard (precision/recall/F1, TP/FP/FN, per-class, p50/p95 latency) and **lessons → product** (FP bias → tighten threshold/zone, recall gap → grow data). The legacy 2D agent replay viewer remains under the *Agent Replay* tab. (Phase 3/4 — see `docs/phase4/01-inference-serving-and-cv-simulation-design.md` and `backend/services/simulation_service/`.)
- Dashboard otherwise functional: map (MapLibre), assets, alerts, control, admin, simulation. Helm templates are minimal and Terraform is placeholder. Compose is the supported deployment today.
- Real-airframe flight has not been commanded on hardware; that requires PX4 SITL (see `docker-compose.sitl.yml`) then a Pixhawk + Jetson Orin (TensorRT FP16/INT8, ≥20 fps @1280). The full command path (mission service → drone bridge → MAVSDK) is proven in SITL; edge serving and on-device re-certification are Phase 4 work.
- `assets.region_id` is retained as a deprecated legacy bridge alongside `site_id` and remains in the schema for backward compatibility with the asset service. It is marked for removal; the canonical path is `site_id`.

---

## Project Structure

```
backend/
  services/          # FastAPI: gateway, auth, asset, telemetry, inference, ml, alert, control, drone_bridge, mission, simulation, detections
  shared/            # defense_shared (schemas, security, middleware)
  ml/                # prepare.py, build_corpus.py, train.py, eval.py, config/dataset.yaml
  db/schema/*.sql    # Timescale + PostGIS, 86+ tests live
  tests/             # 100 tests incl. eval_gate + simulation (FakeTransport)
dashboard/           # Next 16.3 / React 19 / MapLibre / three.js / Zustand / Recharts
  src/app/dashboard/simulation/page.tsx   # CV Lab (L1/L2/L3) + legacy replay
  src/components/simulation/BrowserWorld.tsx  # three.js FPV world → ingest
simulation/          # legacy: sensor_emulator (stub), scenario_runner, scenarios/railway_line.json
docs/
  phase0/  phase1/  phase2/  phase3/  phase4/  phase5/  architecture/  security/
manifests/frozen-visdrone.json   # content hashes, benchmark quarantine
runs/detect/runs/visdrone-s{1,2}/  # training artifacts (curves, weights)
scripts/             # run_local.sh, run_sitl.sh, seed_admin.py, gen_jwt_dev.py, verify_health.sh
infra/helm  infra/terraform  infra/kubernetes
```

---

*Every model that flies is traceable. Every alert is explainable. Every simulation exercises the same pipeline the aircraft will fly.*
