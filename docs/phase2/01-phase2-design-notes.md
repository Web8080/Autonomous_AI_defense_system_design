# Phase 2 Design Notes — Model Registry, Evaluation Gate, Training Pipeline

Phase 1 made the control plane real: a model's *decision* can be carried from a
human-approved flight to an actual aircraft. Phase 2 makes the *model itself*
addressed: which model is flying, who certified it, against what data, and how
a better one replaces it without silently regressioning the metric that keeps a
site switched on.

A deliberate boundary up front: **Phase 2 built the certificate layer and the
pipeline scaffolding. It did not train a model.** No VisDrone data was
downloaded, no YOLO weights produced. Everything below that claims to be
"live" is live; everything that needed a GPU and the dataset is stated as
not-done, in the open, at the end.

---

## 1. The dataset decision

The intended training/evaluation data is **VisDrone-DET** (AISKYEYE,
VisDrone2019), a large-scale drone-captured object-detection benchmark. Classes
pinned in `backend/ml/config/dataset.yaml`:

```
ignored regions, pedestrian, people, bicycle, car, van, truck,
tricycle, awning-tricycle, bus, motor, other
```

Why VisDrone: the imagery is genuinely **from a drone** (altitude, scale,
crowding, occlusion), which matches the operating envelope far better than
COCO/VOC ground imagery. The product talks about intruders, trespass, vehicles
and equipment — VisDrone classes (person, car, truck, van, bus, bicycle, motor)
are the right vocabulary to start from, and "person"/"car" are the two classes
the promotion-gate tests and the frozen benchmark name assume.

**What must be said plainly:** `config/dataset.yaml` records the *intention and
the class vocabulary*. No VisDrone images have been downloaded into this repo,
no dataset (`dataset_root`/`benchmark_root` paths in the config) exists yet, and
therefore no benchmark hash has ever been computed. Phase 2's quarantine
tooling will refuse to certify anything that claims otherwise — by design.

Data review before training: VisDrone is published by AISKYEYE for non-commercial
research; a commercial perimeter product must verify the licence, or substitute
its own labelled flight corpus. The dataset hash + benchmark manifest mechanism
exists to record whatever is actually used, regardless of which source wins.

---

## 2. What was built (and is live)

### 2.1 `ml-service` — model registry (`backend/services/ml_service/`)

The Phase 0 `ml.models` / `ml.model_evals` tables stop being dormant:

- **`POST /models`** registers a model with full provenance: git SHA, content
  dataset hash, artifact URI, runtime/precision, class names. `task`,
  `runtime`, `precision` are `Literal`-constrained at the pydantic layer too.
- **`POST /models/{id}/evals`** records an evaluation. `passed_gate` and
  `gate_notes` are **computed on the server** by `eval_gate.py`; a client that
  insists it passed cannot win. The gate also folds in the regression check at
  eval time (see 2.2).
- **`POST /models/{id}/promote`** is the money path: requires a passing eval on
  record (else 403), re-runs the regression check against the *current*
  production model (the incumbent may have changed since the eval was recorded),
  then in one transaction sets this model `production` and archives the prior
  production model. **Exactly one production model exists at any moment.**
  Extended class with `visited_by` references `auth.users` — a recorded human
  promotion decision, structurally identical to the flight-approval gate.
- **`GET /models/production`** returns the certified model with its passing
  eval attached. This is the endpoint the inference service asks for.

### 2.2 The promotion gate (`eval_gate.py`) — pure, frozen, fail-closed

| Criterion | Default | Notes |
|---|---|---|
| benchmark identity | must equal `visdrone-detect-frozen-2026-09` | contamination guard lives here AND at freeze time |
| benchmark hash | must be present | otherwise "which data?" is unanswered |
| `map_50` | ≥ 0.40 | supporting context, not the product metric |
| `recall_at_conf` | ≥ 0.70 | |
| **`recall_at_target_far`** | ≥ 0.80 | product metric #1 |
| **`false_alarms_per_hour`** | ≤ 2.0, and > 0.0 | product metric #2; zero is a broken corpus, not a win |
| `latency_p95_ms` | ≤ 150 | must be measured on the target device |
| per-class recall | ≥ 0.60 for **every** claimed class | a model blind to one class does not fly |

Fail-closed rules pinned by tests: any NULL/NaN/inf metric is a rejection; a
claimed class with no per-class entry is a rejection; unclaimed classes are
ignored. `check_regression()` refuses promotion if the new model is worse than
the incumbent on either product metric — "never a step backwards on the numbers
that keep the site switched on."

### 2.3 Benchmark quarantine (`data_versioning.py`)

- `hash_dataset()` — content hash (path + size + bytes), deterministic.
- `freeze_benchmark()` — refuses to freeze a train/benchmark split if *any*
  file stem overlaps (renaming a leaked image cannot hide it), writes a JSON
  manifest with both hashes. The manifest's `poison` field is a standing
  warning that the benchmark is never to be trained on.

### 2.4 Training / eval harness (`backend/ml/`)

- `train.py` — seeded, pinned-deps training; writes a manifest (git SHA,
  dataset hash, artifact SHA256). Optionally re-checks quarantine before
  starting. **Needs a GPU box; not run here.**
- `eval.py` — runs `model.val()` on the frozen benchmark for mAP/per-class/
  latency, and requires a **temporal flight-corpus manifest** to compute the
  two product metrics (conf-threshold sweep over continuous segments, FAR = FP
  per flight hour, recall recorded at the threshold that keeps FAR ≤ target).
  Without a corpus those fields are null → the gate rejects. This encodes the
  honest point: *the product metric cannot be measured on a folder of pretty
  images.*

### 2.5 Inference pipeline (the traceability promise, made real)

- The inference service resolves the production model from the registry on
  startup; every detection is stamped `model_id`/`model_version`, and bbox is
  now normalised to `[0,1]` xyxy (matching the `detections.detections` schema;
  the raw pixels move into `metadata.bbox_px`).
- **Real bug fixed:** the old stub returned `confidence=0.85, threat_score=0.7`
  — enough to trip the alert threshold (`0.7`) and create *operational alerts
  for a class called `stub_threat`*. Stub mode is now inert: `threat_score=0.0`,
  `metadata.provenance="stub"`, and the alert consumer independently discards
  `stub` provenance before alerting (defense in depth).
- New `detections-consumer` (service `detections_service/`) persists
  `detections.detections` — the training-data capture path. It refuses rows
  without model provenance and any `stub` detection: unprovable detections
  cannot become labels.

### 2.6 Wiring

- Gateway routes: `/api/v1/models` (CRUD, RBAC), `/models/production`,
  `/models/{id}/evals`, `/models/{id}/promote` (**audit-logged**, super-admin
  only, requires `promoted_by`).
- Compose: `ml-service` (8011) and `detections-consumer`; `inference-service`
  points at `ml-service`. CI builds both images and runs the new tests.
- `scripts/run_local.sh` boots the new services.

---

## 3. Verified, with evidence

- **86/86 unit tests pass** (16 new): gate fail-closed matrix (incl. NaN),
  regression never-worse rule, contamination rejection incl. renamed leaks,
  deterministic/content-sensitive hashing, registry schema validation,
  stub-inertness.
- Live end-to-end against real Postgres (now cleaned up):
  1. weak model + failing eval → `passed_gate=false`, notes list every reason;
  2. promote with no passing eval → **403**;
  3. passing eval → promote → `production`, `promoted_by` recorded;
  4. model with passing absolute metrics but worse FAR than incumbent → **409**
     (regression refused);
  5. strictly-better model → promoted, incumbent auto-archived;
  6. bogus `promoted_by` UUID → clean **400** (FK handler).
- `ml_service` and `detections_service` images build; the containerized
  registry reached the real DB and returned `/models/production`.

---

## 4. What is NOT done yet (stated in the open)

- **No dataset.** VisDrone has not been downloaded, licensed, converted to YOLO
  format, or hashed. The frozen benchmark manifest (`benchmark_hash`) has never
  been written. `config/dataset.yaml` points at paths that do not exist.
- **No trained model.** `train.py` is written but not run (needs a GPU and the
  dataset). There are zero rows in `ml.models` after test cleanup.
- **No temporal flight corpus** → `false_alarms_per_hour` and
  `recall_at_target_far` have never been measured for any real candidate. The
  gate exists to make this omission loud, not quiet.
- **No artifact storage.** `artifact_uri` is recorded but the inference service
  still loads on-disk weights via `MODEL_PATH`; nothing pushes/pulls S3. The
  registry describes artifacts; it does not host them yet.
- **MLflow is referenced, not run.** `mlflow_run_id` is stored; there is no
  MLflow server and the ID cannot be resolved. Decide before Phase 3 whether the
  schema-as-registry is enough (likely, for now).
- **Real-detection alert chain not exercised end-to-end.** With no real model,
  no real detection rows→`detections.detections`and no real alert has flowed
  through the stack. The consumer's boot and DB connection are verified; message
  persistence is not.
- **`eval.py` per-class recall** is populated via ultralytics internals that are
  not yet exercised against a real run; if it comes back empty, the gate rejects
  (fail-closed), so the risk is "no certify", not "wrong certify".
- **Operator review loop (active learning)** — the `review_state`/`corrected_class`
  columns exist and the capture path is built, but no UI or API yet records
  operator corrections back into the next dataset. That is Phase 3 material.

---

## 5. Design notes for Phase 3 hand-off

- The single source of truth for "what flies" is
  `GET /ml/models/production`. Everything that consumes detections should read
  it, not guess.
- Anything that can create an alert must treat `stub`-provenance input as toxic.
  The pattern (producer labels, consumer verifies) should survive into Phase 3.
- mAP is not the product metric, and a candidate that cannot prove
  FAPFH/recall-at-target-FAR on a temporal corpus cannot be promoted — keep any
  Phase 3 model dashboard honest about this.
- Promote/archive atomicity and the recorded human decision are the parts a
  regulator will actually look at. Don't add UI that lets a promotion happen
  without `promoted_by`.

---

## 6. Making the computer vision smart — evidence, not vibes

The model work is a YOLOv8n fine-tune on VisDrone, but the "smart" part is the
discipline around it. The pipeline implements six choices that measurably
matter in this domain:

1. **Two-stage transfer learning.** VisDrone is a different world from COCO:
   top-down views, dense crowds, objects 5–40 px. Freezing the backbone
   (layers 0–9, up to SPPF) and training the detection head first lets the head
   learn aerial scale/spacing before the backbone's batch-norm statistics are
   re-adapted at low LR in stage 2. BN statistics transfer badly between
   ground and aerial imagery; this is why stage 1 freezes them.
2. **Class vocabulary as feature engineering.** Raw VisDrone has 12 categories;
   we dropped `ignored regions` (an ignore directive, not a class — keeping it
   poisons mAP) and `other` (too ambiguous, never an alarm class), remapping
   `1..10 → 0..9`. That is why `prepare.py` re-writes the labels and the frozen
   hash covers the *cleaned* data while `source_hashes` records the raw
   download — provenance covers both layers.
3. **Honest label math.** Every converted box is clamped to image bounds and
   zero-area leftovers dropped; the class report (`report.json` — car 14,064 vs
   bus 251 val ground truths) is what made the per-class gate floor
   frequency-tiered instead of aspirational.
4. **The product metric is temporal.** mAP is context. The gate is
   `recall / false_alarms_per_hour` at a swept threshold on a flight corpus.
   The corpus here is *pseudo-flight* (sequence-aligned VisDrone frames at
   nominal 5 fps). The numbers it produces are indicative FAPFH on never-trained
   aerial data — honest placeholders until real site footage flows through the
   detections capture path.
5. **Operating point, not gut feeling.** Recall-at-target-FAR is chosen by
   sweeping confidence and reading off the threshold that keeps FAR ≤ 2/hr,
   then recall is reported *at that threshold*. This is the number the alert
   service should later use per class, replacing the single hard-coded 0.7.
6. **Deterministic post-processing is the safety, not the network.** Tracking
   (ByteTrack) and zone containment (PostGIS) are deliberately non-ML per the
   roadmap; the alert layer is where deterministic confirmation happens, so an
   operator and a regulator can always be told exactly why an alert fired.

### Scaling the CV further (roadmap forward)

- **Small-object recall** is the dominant failure mode at patrol altitudes.
  Published VisDrone numbers are stuck around mAP@50 0.28–0.44 for YOLOv8n/s.
  The lever that moves recall at a per-pixel level is input resolution
  (imgsz 1280 on the Orin via TensorRT) or SAHI sliced inference for the very
  small tail. Both are Phase 4 (edge) work; the eval harness already emits
  `per_slice` for SAHI later.
- **Rare-class recall** (bus, truck, tricycle, awning-tricycle, bicycle) is a
  data-imbalance problem first. Options in order of cost: class-weighted loss,
  oversampling of rare sequences during training, then targeted data. The gate
  enforces a *floor* (tiered by `gt_count`), which measures the ceiling on what
  the data can certify.
- **Night operations.** VisDrone is daytime imagery; the product patrols at
  night. Augmentation (brightness/contrast/noise) is a cheap stand-in, but the
  honest fix is a night corpus — the detections capture path exists to grow
  exactly that. Night performance must be treated as *unvalidated* until then.
- **Confidence calibration.** YOLO confidence is not a probability. Before the
  umber alert threshold is set per class, a temperature/Platt calibration on
  the frozen val split maps confidence onto measured false-alarm rate, making
  "confidence 0.6" mean something an operator can trust.

## 7. Alert triggering — how it works today, and the gap to the plan

**As built (Phase 2, end-to-end but minimal):**

1. Drone bridge / camera → frames land on `inference.frames` (or `/infer`).
2. Inference service runs the production model; each detection is stamped
   `confidence`, `threat_score`, normalized `bbox`, `model_version`,
   `provenance` (`production` / `unregistered` / `stub`).
3. Detections are published to `inference.detections`.
4. `detections_service` persists rows to `detections.detections` — the future
   training-label corpus. It **refuses** rows without provenance and all
   `stub`-provenance rows: unprovable detections cannot become labels.
5. `alert_service` independently re-checks provenance (defense in depth) and
   creates an alert when `threat_score ≥ 0.7` (medium) or `≥ 0.8` (high).

**Gap to the roadmap (flagged for Phase 3):** the alert layer is a
*per-frame confidence threshold today*. It does not yet use the two things the
roadmap says alerting must use: **zone containment (PostGIS)** and **temporal
confirmation (tracking)**. Without them, a site gets one alert per frame per
pass of the same static object — the exact "cries wolf 40× a night and gets
unplugged" failure the product metric exists to prevent. The steps, in order:

1. Per-class operating thresholds from the FAPFH sweep (replaces the single 0.7).
2. Temporal N-of-M confirmation on a track (ByteTrack): alert only when a track
   persists ≥ N frames above its class threshold — kills flicker and single-frame
   sensor noise while keeping true events (they persist across frames naturally).
3. Zone rules: detection inside a restricted polygon + threat class → alert;
   outside → log only. PostGIS containment, deterministic and explainable.
4. Alert coalescing keyed on `(asset, zone, class, track_id)` so a static
   intruder alerts once, not every pass.
5. Edge redaction before detections leave the aircraft (blur faces/plates).

## 8. Edge cases, trade-offs, limitations, failure modes

| # | Edge case / failure mode | Why it matters | Mitigation (built or planned) |
|---|---|---|---|
| 1 | Benchmark contamination (train→val leak, incl. renamed files) | Certified numbers lie, regulators don't | `freeze_benchmark` refuses on path **or stem** overlap; `train.py` re-checks the same at run time; content-hash covers bytes |
| 2 | Domain shift: COCO→aerial→**night** | Benchmark is daytime aerial; product is night | Two-stage transfer (done); night corpus via capture path; night performance declared unvalidated until then |
| 3 | Dense/occluded frames up to 317 objects | `max_det=300` default caps recall on exactly the frames that matter | Harness auto-raises `max_det` to observed max; alert path caps sensibly; SAHI in Phase 4 |
| 4 | Tiny objects (5–40 px) | The majority of real threat objects | imgsz 1280 / SAHI on edge; `per_slice` metric reserved in eval |
| 5 | Class imbalance (val: bus 251, car 14k) | Uniform per-class floor makes rare classes literally uncertifiable | Tiered floor by `gt_count` proven in the eval; loss weighting next |
| 6 | NaN/inf/NULL metrics | Silent pass-open | Gate fails closed on any of these (`_bounded`); zero-FAR also rejected as a broken corpus |
| 7 | Latency measured on the wrong device | MPS/CPU numbers ≠ Jetson/TensorRT | Record `device` in the eval; MPS p95 is a smoke bound, not the Orin certification — Phase 4 re-certifies |
| 8 | FP32→FP16/TensorRT silent behaviour change | 0.3% mAP drop is fine, a bias shift isn't | Re-eval after quantize/export before deployment; same frozen gate |
| 9 | Stub/unregistered detections | Fabricate alerts or poison labels | Producer stamps, both consumers independently verify provenance; unprovable = discarded |
| 10 | Alert flicker / re-alert per pass | Site unplugs the system | Temporal N-of-M, tracking, zone rules, coalescing (see §7) |
| 11 | Pseudo-flight corpus is not a real flight | FAPFH is indicative, not certified | Stated in the corpus manifest and in every eval record; real-flight data is the Phase 3+ capture-path outcome |
| 12 | Confidence ≠ probability | A threshold of 0.7 is folklore until calibrated | Calibration step (§6) maps conf→measured FAR per class |
| 13 | VisDrone licensing | Research-only; redistributing it in this repo would be a violation | Raw data and zips are gitignored; `manifests/` carries hashes only |
| 14 | Training dies mid-run / OOM | Wasted hours, no artifact | Stage outputs + logs; resume from `last.pt` with `--weights`; seed/non-deterministic-MPS caveat noted |

---

*Dataset intended: VisDrone-DET. Dataset actually used: none yet. That sentence
is the design note, not the whole document.*