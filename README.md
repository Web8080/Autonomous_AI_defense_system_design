# Autonomous AI Defense System for Critical Infrastructure

## Purpose and Product Context

This system provides integrated autonomous surveillance and response for critical infrastructure. It detects threats in real time from fused sensor data (cameras, LIDAR, radar, IoT), runs AI-powered computer vision (YOLO/PyTorch), and supports autonomous deployment of drones and ground vehicles within safety constraints. Operators (Super Admin and Local Operator) monitor, intervene, and audit all actions.

Primary users: security operators who need full visibility and override; local operators who supervise a region; and the System AI agent that makes bounded autonomous decisions. The system is built for local simulation and emulation first, with optional cloud (AWS) and hardware integration via placeholders and explicit credential requests.

## Architecture Overview

- **API Gateway**: Auth, RBAC, REST and WebSocket; proxies to backend services.
- **Asset Service**: CRUD and status for drones, vehicles, sensors; region-scoped for local operators.
- **Telemetry Service**: Ingest from Kafka, aggregate, store in PostgreSQL, serve queries.
- **Inference Service**: YOLO/PyTorch threat detection; consumes frames from Kafka or HTTP, publishes detections.
- **Alert Service**: Consumes detections and rules; creates alerts; persists and notifies.
- **Control Service**: Emergency stop, override, path plan; audit log; MQTT/WebSocket/ROS placeholders.
- **Dashboard**: Next.js; map, assets, alerts, control, admin (Super Admin); login and role-based views.
- **Simulation**: Agent and sensor emulators publish to Kafka for testing without hardware.
- **AI Pipelines**: Training and fine-tuning scripts (local/S3/Roboflow); drift detection and retrain triggers.

Data flow: sensors and simulators push telemetry to Kafka; telemetry service persists aggregates; inference consumes frames and produces detections; alert service creates alerts; control service issues commands to assets (real or simulated). All credentials and cloud/hardware access use env placeholders; operators must supply secrets before use.

Tech stack: PyTorch, YOLO (ultralytics), FastAPI, PostgreSQL, Redis, Kafka, Docker, Kubernetes, Terraform, AWS (optional), Celery, Prometheus/Grafana (scaffolded).

## Key Workflows

- **Threat detection**: Telemetry/frames -> Kafka -> inference -> detections -> alerts and optional autonomous agent -> control commands.
- **Operator override**: Dashboard -> API gateway -> control service -> audit log and MQTT/asset.
- **Emergency stop**: Dedicated endpoint -> control service -> immediate command to assets; always logged.
- **Training and drift**: Local or S3/Roboflow datasets -> train_yolo / finetune; metrics -> drift_detector -> optional retrain_trigger.

See `docs/phase0/07-ai-decision-flows.md` for detailed AI and operator flows.

## Setup Instructions

1. **Prerequisites**: Docker and Docker Compose, Python 3.11, Node 20 (for dashboard), Kafka (included in Compose).
2. **Clone and env**: Copy `.env.example` to `.env` and set at least `DATABASE_URL`, `REDIS_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `JWT_SECRET`. Do not commit `.env`.
3. **Backend**: From repo root, run `./scripts/run_local.sh` to start Postgres, Redis, Kafka, and all backend services. Or run `docker compose up -d` then start services manually.
4. **Database**: Schema is applied via `backend/db/schema/01_init.sql` when Postgres first starts (Docker volume).
5. **Dashboard**: `cd dashboard && npm install && npm run dev`; open http://localhost:3000. Set `NEXT_PUBLIC_API_URL` if API is not on localhost:8000.
6. **Verification**: `./scripts/verify_health.sh` (requires API on port 8000).

## Security and Safeguardrails

- **OWASP Top 10**: Mitigations are documented in `docs/security/OWASP-TOP10.md` (access control, crypto, injection, rate limit, security headers, SSRF, audit logging, etc.).
- **AI safeguardrails**: Intent allowlist, forbidden intents, payload size/depth limits, inference input caps and URL allowlist. See `docs/security/AI-SAFEGUARDRAILS.md`.
- **Auth**: JWT with issuer/audience/expiry; set `JWT_SECRET` (32+ chars). Local dev: set `ALLOW_DEV_TOKEN=true` and use Bearer `dev-token` from dashboard when API is localhost. Generate a real JWT with `scripts/gen_jwt_dev.py` if needed.

## Configuration

- **Required**: `DATABASE_URL`, `REDIS_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `JWT_SECRET` (32+ chars), `JWT_ALGORITHM`. See `docs/phase0/06-environment-and-secrets.md`.
- **Optional**: AWS_* (S3, credentials), ROBOFLOW_* (datasets), MQTT_* and DRONE_* (hardware). Never commit secrets; use `.env` or a secret manager.
- **Dashboard**: `NEXT_PUBLIC_API_URL` for API base URL.

## Failure Modes

- **Kafka down**: Telemetry and inference ingestion pause; replay after recovery. Emergency stop path does not depend on Kafka.
- **Inference down**: No new detections; alerts and control still work; operators can override.
- **Postgres down**: All services that persist state are unavailable; bring DB back and restart services.
- **Control service down**: Commands and emergency stop fail until restored; design heartbeat so assets can enter safe state if control is lost.

## Debugging Tips

- **API 502**: Backend service unreachable; check Docker Compose or service URLs in gateway env.
- **Dashboard "API unreachable"**: Ensure backend is up and `NEXT_PUBLIC_API_URL` matches.
- **Tests**: Backend unit tests from `backend/`: `pip install -r requirements.txt && pytest tests/`. Some tests expect DB; use Docker Postgres or ignore DB-dependent tests.
- **Simulation**: Run `./scripts/run_simulation.sh` after Kafka is up; check Kafka topics with `kafka-console-consumer`.

## Explicit Non-Goals

- No lethal or irreversible autonomous action; all high-impact actions require operator approval or are out of scope.
- No production deployment or hardware connection without operator-provided credentials and approval.
- This scaffold does not replace full auth (e.g. NextAuth); login is placeholder for development.

## Known Debt

- JWT validation in API gateway is stub (accepts any Bearer token).
- Inference service runs stub detections when no model is mounted; wire real YOLO and MODEL_PATH for production.
- Helm chart has minimal templates; expand per service for full K8s deploy.
- Terraform has placeholder resources; add EKS, RDS, S3, IAM after account/region are approved.
- E2E tests assume backend and dashboard running; CI runs unit tests and dashboard build only.

## Screenshots

### Dashboard map
![Operations map with assets and alerts](./screenshots/dashboard-map.png)

### Control and emergency stop
![Control page with emergency stop buttons](./screenshots/dashboard-control.png)
