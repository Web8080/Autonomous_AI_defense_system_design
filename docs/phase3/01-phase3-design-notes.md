# Phase 3 Design Notes — Dashboard Rebuild & CV Simulation Lab (Live Pipeline)

**Status:** Done (CV Lab shippable, map + simulation on the real inference path)

Phase 2 made the *model* addressable (registry, gate, corpus). Phase 3 makes the *operator* see the model in the loop — live map, live video, and a simulation harness that exercises the same pipeline the aircraft flies.

---

## 1. What Phase 3 delivers

| Capability | Before | After |
|------------|--------|-------|
| Simulation | `sensor_emulator` stub (red square), `scenario_runner` 2D coords, empty Gazebo | **CV Lab L1/L2/L3** all publish to `inference.frames` so trained YOLO → detections → persistence → alerts are real |
| Dashboard simulation | Topological 2D replay only (path + threats) | **Layer pills** ① Real footage ② Synthetic ③ 3D world, video + GT vs detection overlay, live scoreboard, latency, lessons |
| Video | No live video | `recent_frames` JPEG (up to 640px) polled from `simulation_service`, overlay of TP (green ✓) / FP (red) / GT (dashed blue) |
| Scoreboard | mAP only (offline) | Live `precision/recall/F1, TP/FP/FN/GT, per-class (8), alert candidates, p50/p95, scoring drain` |
| Lessons → product | Not measured | `false_positive_bias` → tighten threshold/zone, `recall_gap` → grow data, `alert_pressure` — all from live scoring |
| Legacy | — | Preserved under *Agent Replay* tab (map/drone views, trails, zones) |

---

## 2. Architecture Delta

* **New service** `backend/services/simulation_service/` — `engine.py` (ExerciseManager + pump thread), `scoring.py` (IoU 0.5, greedy same-class, alert ≥0.7), `frames_l1.py` (CorpusSource → 640px JPEG + normalized GT), `frames_l2.py` (procedural 960×540 world, SCENARIOS `railway-yard`/`market-square`, deterministic seed, clamp + drop off-frame), `transport.py` (`KafkaTransport` on `inference.frames`/`inference.detections` + `FakeTransport` for tests), `main.py` (FastAPI: `POST /exercises` {layer,scenario,fps,sequence,frames}, `GET /exercises/{id}`, `POST …/stop`, `POST …/ingest` for L3).
* **Gateway** `api_gateway/main.py` — proxy `GET /api/v1/simulation/layers|exercises|exercises/{id}` + `POST …/exercises|…/stop|…/ingest` (JWT, RBAC, 502→CORS).
* **Dashboard** `dashboard/src/lib/api.ts` — `getSimulationLayers`, `listSimulationExercises`, `createSimulationExercise`, `getSimulationExercise`, `stopSimulationExercise`, `ingestSimulationFrame`.
* **Dashboard page** `dashboard/src/app/dashboard/simulation/page.tsx` — tabs *CV Lab* vs *Agent Replay*. CV Lab: layer selector, sequence/scenario/fps/frames, Start/Stop, exercise strip, poll `GET /exercises/{id}` every 900ms, `FrameOverlay` (GT + detections), `BrowserWorld` for L3. Legacy tab keeps the 374-line Canvas replay intact.
* **Dashboard 3D** `dashboard/src/components/simulation/BrowserWorld.tsx` — `three@0.160`, 640×360, fog, shadows, ground (3a4a2b), roads (boxes), 5 buildings, 6 people (cylinder+sphere+shadow, 1.7m), 3 vehicles (boxes), drone patrol 12 m/s @38 m, Perspective 58°, `project(camera)` → normalized GT, `toDataURL('jpeg',0.78)` → ingest. Throttled to `ingestFps=5`, `L3_CAP 4096`, `MAX_EXERCISES 8`.
* **Compose** `docker-compose.yml` — `simulation-service:8014` (volume `data/visdrone-yolo/corpus:/mnt/corpus:ro`, `SIM_CORPUS_MANIFEST`), gateway env `SIMULATION_SERVICE_URL`, `scripts/run_local.sh` boots it, `docker-compose.sitl.yml` unchanged.

All three layers share the **same Kafka contract** a real camera does: `{asset_id, frame_id, timestamp, image_b64, source: "simulation/l{1,2,3}"}` → inference → `{detections:[{class_name, confidence, bbox:[0,1], threat_score, model_version, metadata:{provenance,bbox_px}}], frame_id}` → scoring keyed by `frame_id` (`id:idx`). Stub provenance never scores (defense in depth).

---

## 3. Why it matters for the real product

* **Hardware focus:** The lab exists to de-risk the Orin flight — `his is the handler pass second adoption second Copy responds synthetic checks def uns how effect carried ge back resolution concepts psy---`
* **Evidence:** A sequence is a pseudo-flight (5 fps, `hours = frames/fps/3600`); `FAPFH = FP / hours` at the operating point is the number the site cares about (Phase 2 §6). L1 is *real* footage (VisDrone) — no synthetic risk. L2 is *exact* GT for rare-class what-ifs. L3 is *photoreal-ish* but still through TensorRT.
* **Data flywheel:** Every `detections.detections` row (refusing stub/unregistered) is a future label. The simulation is also a **synthetic-data engine** (L2) for bus/truck imbalance and night.

---

## 4. Testing & SLOs

* `backend/tests/test_simulation_service.py` 14 tests: `TestScoring` (IoU, TP/FP/FN, alert threshold, aggregate), `TestL2Synthetic` (JPEG magic, determinism, clamp 0..1), `TestEngine` (pump 5 frames → completed, detection tallied, L3 stop), `TestMainRoutes` (create L2, 404).
* SLOs carried from Phase 4 design: detection→row <250 ms, camera→alert <1.5 s (Orin). MPS numbers are smoke only; `latency_ms.p95` is measured on the declaration `device` and must be re-certified on Orin.

---

## 5. Files

```
backend/services/simulation_service/{main,engine,scoring,frames_l1,frames_l2,transport}.py
dashboard/src/lib/api.ts                         # simulation helpers
dashboard/src/app/dashboard/simulation/page.tsx  # CV Lab + legacy
dashboard/src/components/simulation/BrowserWorld.tsx
docker-compose.yml  scripts/run_local.sh  .env.example  .github/workflows/ci.yml
```

*Documented against the repo at the end of the Phase 3 CV Lab build.*
