# Drone Infrastructure Monitoring

![Banner](.github/social-preview.jpg)

Autonomous drone patrol and anomaly detection for critical infrastructure: rail corridors, electrical substations, solar and wind farms, ports, reservoirs and industrial perimeters.

Drones fly scheduled or operator-triggered missions. Onboard computer vision detects intrusions, trespass, vegetation encroachment and equipment faults. Operators get a live map, live video, an alert queue and a full audit trail. **Every dispatch is approved by a human before the aircraft arms.**

## What this replaces

Sites of this kind are inspected today by walking patrols, manned vehicle rounds and periodic helicopter or rope-access surveys. Those are expensive, infrequent and dangerous. A drone flying a fixed patrol four times a night covers more ground, more often, with a consistent record of what it saw.

## Product boundary

This system observes and alerts. It does not take autonomous physical action against anything.

- No lethal, kinetic or irreversible autonomous action, ever.
- No autonomous dispatch without a named human approving it first. The approval is recorded against the flight.
- No real-time biometric identification. Faces and number plates are blurred at the edge by default.

These are product constraints, not defaults to be configured away.

## Architecture

```
Dashboard (Next.js)  ──REST/WS──▶  API Gateway (FastAPI, JWT + RBAC)
                                        │
        ┌───────────┬───────────┬───────┴───┬───────────┬───────────┐
      Auth       Asset      Mission      Alert     Telemetry    Control
                                        │
                              Redpanda (Kafka API)
                                        │
                            ┌───────────▼───────────┐
                            │   Drone Bridge        │
                            │   MAVLink / MAVSDK    │
                            └───────┬───────┬───────┘
                                    │       │
                        PX4 SITL + Gazebo   Pixhawk + Jetson Orin
                          (dev and CI)      (edge inference, TensorRT)
```

**Services**
- **Auth** — issues short-lived access JWTs and rotating, revocable refresh tokens. Accounts are provisioned, not self-signup.
- **API Gateway** — verifies JWT signature, issuer, audience and expiry; enforces RBAC; forwards site scoping downstream.
- **Asset** — drones, ground vehicles, sensors. Site-scoped for operators.
- **Mission** — waypoints, schedules, geofences, monitored zones.
- **Telemetry** — vehicle state at high rate into TimescaleDB hypertables.
- **Inference** — YOLO detection. Runs at the edge in production; cloud only for backfill and evaluation.
- **Alert** — turns detections plus deterministic zone rules into operator alerts.
- **Control** — commands, emergency stop, and the append-only audit trail.
- **Drone Bridge** — MAVLink both ways; geofence, link-loss and battery failsafes.

**Stack:** FastAPI, TimescaleDB + PostGIS, Redis, Redpanda, PyTorch/Ultralytics, TensorRT, MAVSDK, PX4, Next.js, MapLibre, Docker, Kubernetes.

## Design decisions worth knowing

**Zone logic and tracking are deliberately not machine learning.** Whether a detection sits inside a restricted polygon is a PostGIS containment query, and object tracking is ByteTrack. Both are deterministic and explainable. A neural network there would buy nothing and cost the ability to say exactly why an alert fired — which is the thing an operator and a regulator both want to know.

**Inference runs on the aircraft, not in the cloud.** Shipping video over LTE to a cloud GPU is fine in simulation and unworkable in the field. Only detections and thumbnails go up.

**mAP is not the product metric.** The number that decides whether a site keeps the system switched on is *false alarms per flight hour at target recall*. A perimeter system that cries wolf forty times a night gets unplugged regardless of its benchmark score. Model promotion gates on both.

**Every model that flies is traceable.** Model rows carry the git SHA, dataset hash and MLflow run that produced them, and no model reaches `production` stage without a recorded evaluation against the frozen benchmark.

## Setup

1. **Prerequisites:** Docker and Docker Compose, Python 3.11, Node 20+.
2. **Environment:** `cp .env.example .env`, then set `JWT_SECRET` to 32+ random characters. Never commit `.env`.
3. **Start the stack:** `docker compose up -d`. The database image is `timescale/timescaledb-ha:pg16`, which bundles PostGIS — the schema requires both extensions.
4. **Create the first admin.** There is no self-signup; accounts are provisioned.
   Set up the script venv once:
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r scripts/requirements.txt
   ```
   Then seed:
   ```
   DATABASE_URL="postgresql://defense:defense@127.0.0.1:5432/defense" \
     .venv/bin/python scripts/seed_admin.py --email you@example.com --password '<12+ chars>'
   ```
   The script upserts on email, so re-running it resets that account's password
   (useful after the 5-failure, 15-minute lockout).
5. **Dashboard:** `cd dashboard && npm install && npm run dev`, then open http://localhost:3000 and sign in with the seeded account.
6. **Health check:** `./scripts/verify_health.sh`

## Security

- **Authentication:** access tokens are HS256 JWTs verified for signature, issuer, audience and expiry, with a 15-minute TTL. Refresh tokens are stored hashed, rotate on every use, and are revocable. Failed logins lock the account after 5 attempts.
- **Authorization:** RBAC at the gateway; site scoping derived from signed token claims, never from client-supplied headers.
- **OWASP Top 10:** see `docs/security/OWASP-TOP10.md`.
- **AI safeguards:** intent allowlist, payload size and depth caps, inference input caps, SSRF URL allowlist. See `docs/security/AI-SAFEGUARDRAILS.md`.

## Compliance

Operating this system in the UK or EU carries obligations that are engineering work, not just paperwork:

- **EU AI Act** — AI used as a safety component for critical infrastructure is high-risk; core obligations apply from 2 August 2026. The model registry, evaluation gate, dataset versioning and decision-chain audit log exist to satisfy the risk-management, data-governance, logging and human-oversight duties.
- **UK GDPR** — aerial surveillance capable of capturing identifiable people requires a DPIA, refreshed when operations change. Edge redaction is on by default.
- **UK CAA** — Operator ID and Flyer ID required. Remote ID is mandatory for class-marked drones from 1 January 2026. BVLOS operation requires Specific-category authorisation via SORA; assume 3 to 6 months.

## Known gaps

Tracked honestly, because the repo previously overstated its own completeness.

- Drone Bridge Service is not built yet; `control_service._send_to_asset()` still returns without transmitting. **No real aircraft can be commanded.** (Phase 1)
- Inference returns a stub detection when `MODEL_PATH` is unset. There is no trained model yet. (Phase 2)
- No evaluation harness, benchmark set or promotion gate yet. (Phase 2)
- Dashboard is functional but pre-rebuild: no map, no live video, no mission planner. (Phase 3)
- Helm templates are minimal and Terraform is placeholder. Compose is the supported deployment today.
- `assets.region_id` is retained as a legacy bridge alongside `site_id` and will be dropped in Phase 1.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Tenancy schema, real auth, TimescaleDB/PostGIS, Redpanda | In progress |
| 1 | Drone Bridge, MAVSDK, PX4 SITL in CI, mission execution | Next |
| 2 | VisDrone training pipeline, evaluation gate, model registry | |
| 3 | Dashboard rebuild: map, live video, mission planner, triage | |
| 4 | TensorRT edge deployment, Jetson, real airframe | |
| 5 | Multi-tenant SaaS, billing, onboarding | |
| 6 | DPIA, AI Act technical file, model cards, pen test | |
