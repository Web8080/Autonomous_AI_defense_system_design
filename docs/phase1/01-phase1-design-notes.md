# Phase 1 Design Notes — Drone Bridge, Mission Execution, Human-Approved Dispatch

Phase 1 makes the control plane real. Phase 0 gave the system tenancy, real auth,
TimescaleDB/PostGIS persistence and an event bus. Phase 1 adds the piece that was
missing: **an aircraft that can actually be commanded**, plus the mission planning
and dispatch path that carries a human decision from a dashboard all the way down
to a simulated (or, later, real) flight controller.

This document records what was built, why it is shaped this way, and what is
deliberately still open. It is a design note, not a marketing page — where Phase 1
stops short of the product promise, that is stated plainly.

---

## 1. What Phase 1 adds

| Capability | Before Phase 1 | After Phase 1 |
|---|---|---|
| Commanding an aircraft | `control_service._send_to_asset()` returned `"sent"` without transmitting | Commands reach a MAVSDK `System` (or fail loudly and are logged) |
| Mission planning | Schema tables existed (`missions`, `waypoints`, `flights`) but no API touched them | Full CRUD over REST: missions, waypoints, flights |
| Human approval gate | Schema columns existed (`approved_by`, `approved_at`) but nothing enforced them | Rejected with 403 before dispatch unless a named operator approved |
| Vehicle telemetry | `telemetry.aggregated` (free-form) only | High-rate snapshots persisted to the `telemetry.vehicle_state` hypertable |
| Trust boundary below the API | None — no service knew how to talk MAVLink | `drone_bridge` is the only service that imports MAVSDK |
| PX4 safety params | Documented intent only | Envelope pushed to the FC as PX4 params before every mission |

Two new services and one rewritten worker:

```
backend/services/drone_bridge/    Drone Bridge — MAVSDK control plane
backend/services/mission_service/ Mission Service — missions, flights, dispatch
backend/services/telemetry_service/kafka_consumer.py   telemetry.raw -> vehicle_state
```

---

## 2. Dispatch data flow

One path, deliberately simple, so the approval gate and the audit trail are easy
to reason about:

```
Dashboard / operator
   │  POST /api/v1/missions                      (gateway)
   ▼
API Gateway (JWT + RBAC)
   │
   ▼
Mission Service
   │  1. create mission + waypoints → missions.*
   │  2. create flight             → status 'awaiting_approval'
   │  3. approve flight            → records approved_by UUID + timestamp
   │  4. start flight              → 403 unless approved
   ▼
Drone Bridge
   │  POST /vehicles/{id}/mission
   │  - check_mission_plan()        (safety.py)
   │  - apply_fc_safety_params()    (PX4 params on the FC)
   │  - upload mission (MAVSDK)
   │  - arm → execute → monitor →  RTL on any ABORT violation
   ▼
PX4 SITL / Pixhawk
```

State changes are pushed back up the chain:

- MissionService → flight status (`in_flight`, `returning`, `completed`, `aborted`, `failed`)
- Drone Bridge → Kafka `telemetry.raw` at `TELEMETRY_PUBLISH_HZ`
- Telemetry consumer → `telemetry.vehicle_state` (TimescaleDB hypertable)

Every dispatch carries its `flight_id` end to end, which is what makes the
AI-Act decision chain (detection → model → rule → operator decision → command →
outcome) reconstructable later.

---

## 3. Drone Bridge (`backend/services/drone_bridge/`)

### 3.1 Layering

The bridge is strict about what may touch MAVSDK:

- **`connection.py`** — `VehicleLink` per aircraft. Owns the MAVSDK `System`,
  tracks link state, reconnects with backoff. `seconds_since_heartbeat` is
  treated as a first-class signal: loss of heartbeat is the trigger for the
  failsafe path, not a debug aid.
- **`mavlink_bridge.py`** — `MAVLinkBridge`: telemetry subscriptions (position,
  flight mode, armed, health) plus single-shot polls (battery, GPS, in-air) and
  the command surface (arm, disarm, takeoff, land, RTL, emergency stop, kill).
- **`safety.py`** — pure functions, no I/O. Geofence, pre-arm gates, mission-plan
  validation, in-flight guards. Unit-tested as standalone logic.
- **`mission.py`** — `MissionExecutor`, a stateful per-flight lifecycle.
- **`telemetry_publisher.py`** — publishes whole `VehicleState` snapshots to Kafka.
  Degrades gracefully: telemetry loss never interrupts a flight.

### 3.2 Why the safety layer is where it is

`safety.py` states the design principle repeatedly and it is worth re-stating:
**software checks in the bridge process are the SECOND layer, never the only
one.** Anything that must hold when the bridge crashes, the network drops, or
Python stalls is also pushed to the flight controller as a PX4 parameter
(`apply_fc_safety_params`). A check that exists only in Python is a check that
does not exist during the failure modes you actually care about.

The layers, outermost first:

1. Pilot RC kill switch — hardware, always live, not ours to override
2. PX4 onboard failsafes — params we set, enforced by the FC independently
3. `safety.py` — pre-arm gates and in-flight monitoring in the bridge
4. Operator approval gate — a named human before any autonomous dispatch

The approved-mission param lives in the *mission service* database, not in the
bridge, so a misconfigured bridge cannot silently turn the gate off: the gate is
the step that inspects `missions.flights.approved_by`.

### 3.3 Mission lifecycle

`MissionExecutor.run()`:

```
check_mission_plan()            → refuse a bad plan before it reaches the FC
approved gate                    → refuse if no approval recorded
upload mission (MAVSDK)          → set_return_to_launch_after_mission(True)
apply_fc_safety_params()         → write envelope to PX4 params
arm → start mission
watchdog: check_in_flight()      → any ABORT violation → RTL + status report
complete → stop watchdog → status reported to mission service
```

The watchdog and mission execution run as sibling tasks. When the mission
finishes (or RTLs home), the watchdog is cancelled deterministically, so `run()`
always returns a terminal status.

---

## 4. Mission Service (`backend/services/mission_service/main.py`)

### 4.1 Scope

- **Missions & waypoints**: CRUD, site-scoped, with a waypoints replace endpoint
  (`PUT /missions/{id}/waypoints`) so an operator can edit a patrol and re-dispatch.
- **Flights**: one row per execution of a mission by an asset. Tracks
  `trigger_kind` (manual / scheduled / detection), approval record, and
  start/end/abort timestamps.
- **Dispatch**: `start` resolves waypoints, re-checks the approval gate, then
  calls the drone bridge. Errors are written back to the flight row
  (`abort_reason`), not swallowed.

### 4.2 The approval gate

`requires_approval` is a mission attribute, defaulting true. A flight created
against such a mission starts in `awaiting_approval`. It can only move to
`approved` via `POST /flights/{id}/approve` with a real `auth.users` UUID
(FK-constrained). `POST /flights/{id}/start` returns **403** if there is no
approval, before anything MAVLink-shaped is attempted.

This was verified against a live database during the build: unapproved start →
`403`; approved start against an unreachable bridge → `502` *and* the flight row
is updated to `failed` with the reason.

FK violations (e.g. an approver UUID that is not a user) surface as a clean
`400` via an asyncpg exception handler — a raw 500 here would hide the exact
thing a regulator asks about: *who approved, and does that person exist?*

---

## 5. Control Service convergence

`control_service._send_to_asset()` previously returned `"sent"` and the README
flagged it as the reason **no real aircraft could be commanded**. Phase 1
replaces the placeholder with a real async dispatcher:

- `emergency_stop` → `POST /vehicles/{id}/emergency-stop` (single asset)
- `mission_abort` → `POST /vehicles/{id}/mission/cancel`
- `override` → RTL
- other intents are recorded locally until handlers exist

Every command is still written to `audit.command_log` as before.

---

## 6. Telemetry pipeline

The drone bridge publishes whole `VehicleState` snapshots (as produced by
`VehicleState.to_dict()`) including a `timestamp`. The **telemetry consumer**
(`kafka_consumer.py`) was rewritten to persist into `telemetry.vehicle_state` —
the real, compressed hypertable from Phase 0 — with:

- `position` stored as `GEOGRAPHY(POINT)` via `ST_MakePoint`
- upsert semantics (`ON CONFLICT (asset_id, ts) DO UPDATE`)
- org_id resolved from `assets.assets` with a per-process cache when the bridge
  does not supply it (the table is `NOT NULL` on org_id)

The old free-form `telemetry.aggregated` path is no longer the primary store.

---

## 7. API Gateway additions

Authenticated, RBAC-guarded routes proxying to the two new services:

```
GET/POST        /api/v1/missions
GET/PATCH       /api/v1/missions/{id}
PUT             /api/v1/missions/{id}/waypoints
GET/POST        /api/v1/flights
POST            /api/v1/flights/{id}/approve     (audit logged)
POST            /api/v1/flights/{id}/start       (audit logged)
GET             /api/v1/drones
GET             /api/v1/drones/{asset_id}
```

`approve` and `start` write gateway audit-log entries, so the human decision and
the dispatch are both traceable from the edge.

---

## 8. PX4 SITL

`docker-compose.sitl.yml` runs:

- **PX4 SITL + Gazebo** (`px4io/px4-dev-simulation`) — builds and runs a Gazebo
  x500 quad; first build clones PX4 and compiles SITL (10–20 min), later runs
  reuse the build volume.
- **redpanda**, **drone-bridge**, **mission-service** — the same stack, one
  simulated aircraft on `udp://px4-sitl:14540`.

`scripts/run_sitl.sh` sets `DRONE_ASSETS` to a placeholder asset UUID. To match
a real registered airframe, export your own `DRONE_ASSETS` first.

This is a developer/integration tool and is *not* a supported deployment. The
behavioural contract (safety gates, geofence, mission plan validation, watchdog
RTL) is covered deterministically by unit tests; SITL is how you see it fly.

---

## 9. Testing

23 new tests, all deterministic, none requiring MAVSDK:

| File | Coverage |
|---|---|
| `tests/test_safety.py` | haversine/ray-casting, all pre-arm blocks (approval, battery, GPS, link, geofence in/out), mission-plan validation, in-flight ABORT/WARN paths, severity precedence |
| `tests/test_drone_bridge.py` | `VehicleState`, heartbeat staleness recovery, `LinkRegistry` add/replace/remove, `BridgeRegistry` requires a connected link |
| `tests/test_mission_executor.py` | rejects unapproved, rejects bad plans, full execution against a **faked MAVSDK** (upload → arm → start → complete), cancel → RTL |
| `tests/test_mission_service.py` | health, query validation, waypoint schema bounds |

Plus the existing gateway/control/asset/inference tests: **56/56 pass**.
The drone bridge Docker image builds lean (no torch) and boots; the mission
service image builds and boots. `docker-compose.yml` and
`docker-compose.sitl.yml` both pass `docker compose config`.

---

## 10. What was deliberately NOT done (and why)

- **Real hardware flight.** Commanding an actual Pixhawk requires a Jetson edge
  module and specific-category CAA authorisation. SITL is the honest boundary
  until Phase 4.
- **`assets.region_id` drop.** Phase 0 flagged it for removal, but the asset
  service and telemetry filters still query `region_id`. It is now documented as
  deprecated; a coordinated migration that also rewrites those queries is the
  safe way to remove it, not a bare `DROP COLUMN`.
- **Broadcast emergency stop.** `asset_id="all"` is validated and logged but the
  drone bridge has no broadcast primitive today. A fleet-wide kill is a
  ground-control action better handled at the GCS layer; noted so it is not
  accidentally assumed to exist.
- **Mission scheduler.** `schedule_cron` is stored but nothing evaluates cron
  expressions yet. A scheduled trigger (`trigger_kind='scheduled'`) is Phase 2
  work alongside detection-triggered dispatch.
- **Telemetry backpressure / DLQ.** The consumer skips bad messages with a log
  line; production needs a dead-letter topic. No important message is dropped
  silently by the bridge itself, but the store currently is best-effort.

---

## 11. Files changed or added

```
backend/services/drone_bridge/           new service (6 modules + Dockerfile + lean requirements)
backend/services/mission_service/        new service (main.py + Dockerfile + requirements)
backend/services/drone_bridge/__init__.py
backend/tests/test_safety.py             new
backend/tests/test_drone_bridge.py       new
backend/tests/test_mission_executor.py   new
backend/tests/test_mission_service.py    new
backend/services/telemetry_service/kafka_consumer.py   rewritten for vehicle_state
backend/services/telemetry_service/Dockerfile          copies consumer
backend/services/control_service/main.py               real dispatcher to drone bridge
backend/services/api_gateway/main.py                   mission/flight/drone routes
backend/requirements.txt                               mavsdk
docker-compose.yml                      mission-service, drone-bridge, telemetry-consumer
docker-compose.sitl.yml                 new: PX4 SITL stack
scripts/run_local.sh                    boots the new services
scripts/run_sitl.sh                     new: SITL helper
.github/workflows/ci.yml                new services tested + built
.env.example                            new URLs + DRONE_ASSETS
README.md                               roadmap/known-gaps updated
```

---

*Documented against the state of the repo at the end of the Phase 1 build.
Treat this file as the source of truth for Phase 1 architecture decisions; the
roadmap table in the root README tracks overall progress.*