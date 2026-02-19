# Module Dependencies

## Dependency Graph (Summary)

```
dashboard (Next.js)
    -> api-gateway (REST, WebSocket)

api-gateway
    -> asset-service, telemetry-service, inference-service (HTTP), alert-service, control-service
    -> auth / RBAC (internal or auth-service)
    -> audit (write-only)

asset-service
    -> PostgreSQL (assets, status)
    -> (optional) Kafka [produce asset.updated]

telemetry-service
    -> Kafka [consume telemetry.raw, produce telemetry.aggregated]
    -> PostgreSQL (time-series or aggregated tables)
    -> (optional) S3 (raw archive)

inference-service
    -> Kafka [consume inference.frames, produce inference.detections]
    -> Redis (optional cache)
    -> Model store (local volume or S3)
    <- HTTP (optional) from telemetry-service or gateways for sync inference

alert-service
    -> Kafka [consume inference.detections, system.events]
    -> PostgreSQL (alerts, state)
    -> Notification (email, webhook)

control-service
    -> Kafka [optional consume commands, produce command.sent]
    -> PostgreSQL (audit)
    -> MQTT / WebSocket / ROS adapters (placeholders)
    <- API (emergency stop, override, path plan)

simulation
    -> Kafka [produce telemetry.raw, inference.frames]
    <- Kafka [optional consume commands for closed-loop]

training / drift
    -> Dataset (local or S3), Roboflow (optional)
    -> Inference metrics (Prometheus / logs)
    -> PyTorch / YOLO scripts
    (no runtime dependency on core services; batch/offline)
```

## Stack per Module

| Module | Language | DB/Broker | External |
|--------|----------|-----------|----------|
| api-gateway | Python (FastAPI) | Redis (session) | All internal services |
| asset-service | Python (FastAPI) | PostgreSQL | - |
| telemetry-service | Python (FastAPI) | Kafka, PostgreSQL, S3 (opt) | - |
| inference-service | Python (FastAPI + PyTorch) | Kafka, Redis (opt), S3 (opt) | Roboflow (opt) |
| alert-service | Python (FastAPI) | Kafka, PostgreSQL | SMTP, webhook |
| control-service | Python (FastAPI) | Kafka (opt), PostgreSQL | MQTT, WebSocket, ROS (placeholders) |
| dashboard | TypeScript (Next.js) | - | api-gateway |
| simulation | Python | Kafka | - |
| training/drift | Python | Local/S3 | Roboflow (opt) |

## Shared Infrastructure

- **PostgreSQL**: One cluster; separate schemas or databases per service recommended (e.g. `assets`, `telemetry`, `alerts`, `audit`).
- **Kafka**: Single cluster; topics per domain; partition key by asset_id or region_id for ordering.
- **Redis**: Session store, Celery broker, optional inference cache.
- **S3 (optional)**: Datasets, model artifacts, raw telemetry archive.
