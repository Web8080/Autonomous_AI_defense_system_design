# Environment Variables and API Keys

All secrets and credentials must be supplied via environment variables or a secret manager. Never commit real values. Use `.env.example` and placeholder values in code.

---

## Required Before Running Any Service

| Variable | Used By | Description | Example (placeholder) |
|----------|---------|-------------|------------------------|
| `DATABASE_URL` | All backend services | PostgreSQL connection string | `postgresql://user:PLACEHOLDER@localhost:5432/defense` |
| `REDIS_URL` | API, Celery, optional inference | Redis connection | `redis://localhost:6379/0` |
| `KAFKA_BOOTSTRAP_SERVERS` | Telemetry, inference, alert, control | Kafka brokers | `localhost:9092` |
| `JWT_SECRET` | API gateway, dashboard (server) | Signing key for JWT | `PLACEHOLDER_CHANGE_IN_PROD` |
| `JWT_ALGORITHM` | Same | Usually `HS256` | `HS256` |

---

## Optional / Feature Flags

| Variable | Used By | Description | Example |
|----------|---------|-------------|---------|
| `AWS_ACCESS_KEY_ID` | Telemetry, inference, training | AWS credentials | (request from user) |
| `AWS_SECRET_ACCESS_KEY` | Same | AWS secret | (request from user) |
| `AWS_REGION` | Same | e.g. us-east-1 | `us-east-1` |
| `S3_BUCKET_TELEMETRY` | Telemetry service | Raw telemetry archive | `defense-telemetry-prod` |
| `S3_BUCKET_DATASETS` | Training scripts | Dataset storage | `defense-datasets` |
| `S3_BUCKET_MODELS` | Inference, training | Model artifacts | `defense-models` |
| `ROBOFLOW_API_KEY` | Training, optional inference | Roboflow API | (request from user) |
| `ROBOFLOW_WORKSPACE` | Training | Workspace/project name | `critical-infra` |
| `MQTT_BROKER_URL` | Control service | MQTT broker for assets | `mqtt://localhost:1883` |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | Control service | MQTT auth | (request from user) |
| `DRONE_API_BASE_URL` | Control adapter | Vendor drone API | (request from user) |
| `DRONE_API_KEY` | Control adapter | Vendor API key | (request from user) |
| `LIDAR_INGEST_URL` | Telemetry ingest | LIDAR feed endpoint | (request from user) |
| `RADAR_INGEST_URL` | Telemetry ingest | Radar feed endpoint | (request from user) |

---

## Per-Service Env Files

- **api-gateway**: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `JWT_ALGORITHM`, service URLs (asset, telemetry, alert, control), `CORS_ORIGINS`.
- **asset-service**: `DATABASE_URL`.
- **telemetry-service**: `DATABASE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, optional `S3_*`, `AWS_*`.
- **inference-service**: `KAFKA_BOOTSTRAP_SERVERS`, `REDIS_URL` (optional), model path or `S3_BUCKET_MODELS`, `AWS_*`, optional `ROBOFLOW_*`.
- **alert-service**: `DATABASE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `SMTP_*` or webhook URL for notifications.
- **control-service**: `DATABASE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `MQTT_*`, optional `DRONE_*`, `ROS_MASTER_URI` (placeholder).
- **dashboard**: `NEXT_PUBLIC_API_URL`, server-side `JWT_SECRET` or API key for server-to-API calls.
- **simulation**: `KAFKA_BOOTSTRAP_SERVERS` only (no credentials for local sim).

---

## Credential and Hardware Policy

- **Pause and request user approval** before:
  - Using real AWS credentials (beyond placeholders).
  - Connecting to real MQTT brokers, drone APIs, or hardware.
  - Accessing Roboflow or other third-party APIs with live keys.
- **Local development**: Use `.env` with placeholder or local-only values; Docker Compose can set defaults for Postgres, Redis, Kafka without cloud keys.
- **Production**: Use secret manager (AWS Secrets Manager, Vault) and inject into containers; never bake secrets into images.
