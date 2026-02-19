# System Architecture

## High-Level Diagram (Textual)

```
                    +------------------+
                    |   Operators      |
                    | (Super/Local)    |
                    +--------+---------+
                             |
                    HTTPS / WSS
                             |
    +------------------------+------------------------+
    |                   API Gateway / BFF              |
    |              (Auth, RBAC, REST, WebSocket)       |
    +--+--------+--------+--------+--------+--------+--+
       |        |        |        |        |        |
       v        v        v        v        v        v
   +------+ +------+ +--------+ +------+ +--------+ +------+
   |Asset | |Tele- | |Infer-  | |Alert | |Control | |Audit |
   |Svc   | |metry | |ence Svc| |Svc   | |Svc     | |Log   |
   +--+---+ +--+---+ +---+----+ +--+---+ +---+----+ +------+
      |        |         |         |         |
      |        |         |         |         |
      v        v         v         v         v
   +------+ +------+ +--------+ +------+ +--------+
   | PG   | |Kafka | | Redis  | | MQTT | |ROS/WS  |
   |      | |      | | Celery | | (opt)| |(placeholder)
   +------+ +------+ +--------+ +------+ +--------+
      ^         ^
      |         |
   Sensors   Drones/Vehicles (real or simulated)
   (Camera,   (control commands, telemetry)
   LIDAR,
   Radar,
   IoT)
```

## Multi-Agent Control Flow

1. **Telemetry ingestion**: Sensors and assets publish telemetry to Kafka (raw or pre-aggregated).
2. **Inference**: Inference service consumes frames from Kafka (or receives via HTTP), runs YOLO/threat model, publishes detections with threat score to Kafka.
3. **Alerting**: Alert service consumes detections, applies rules and thresholds, creates alerts, persists and notifies; dashboard consumes alert stream.
4. **Decision (System AI)**: Control service (or dedicated planner) consumes detections and asset state; within safety constraints it produces proposed actions (path, investigate, alert). Actions are logged and sent to assets via MQTT/WebSocket/ROS.
5. **Override**: Operator sends override or emergency stop via API -> control service -> same MQTT/WS path; control service marks command as human override and logs.
6. **Fail-safes**: Geofence and heartbeat enforced in control service and/or in asset adapter; emergency stop endpoint can bypass queue for lowest latency.

## Sensor and AI Inference Pipeline

- **Input**: Camera (RTSP/HTTP frames), LIDAR/radar (placeholder schemas), IoT (JSON).
- **Kafka topics**: `telemetry.raw`, `telemetry.aggregated`, `inference.frames`, `inference.detections`.
- **Inference**: Frames from `inference.frames` or HTTP; output to `inference.detections` (bbox, class, confidence, threat_score).
- **Storage**: Telemetry in PostgreSQL (aggregated) and/or time-series store; raw optionally in S3 with retention.

## Telemetry and Dashboard

- **Aggregation**: Telemetry service consumes from Kafka, aggregates by asset/time window, writes to DB.
- **Dashboard**: Next.js app; auth via API; WebSocket or SSE for live map, telemetry, and alerts; REST for historical data and control commands.
- **Map**: Asset positions, planned paths, threat overlays, and alert markers; data from REST and real-time stream.

## Alerting and Escalation

- **Sources**: AI detections, threshold rules (e.g. telemetry bounds), system events (asset offline, inference failure).
- **Flow**: Alert service -> persist -> notify (in-app, email, webhook) and -> dashboard stream.
- **Escalation**: Configurable per alert type and severity; state: new, acknowledged, escalated, resolved.

## Fail-Safes

- **Emergency stop**: Dedicated API endpoint -> control service -> direct command to MQTT/asset adapter; logged; optional heartbeat so assets auto-halt if control is lost.
- **Geofence**: Control service rejects or clips commands that leave allowed polygon; config per asset or region.
- **Model/Inference**: No autonomous lethal action; all lethal or high-impact actions require operator approval (design time constraint).

## Module Dependencies (Directed)

- **API Gateway** -> Asset, Telemetry, Alert, Control, Audit.
- **Asset Service** -> PostgreSQL.
- **Telemetry Service** -> Kafka (consume), PostgreSQL, optional S3.
- **Inference Service** -> Kafka (consume frames, produce detections), Redis (optional cache), model storage (local/S3).
- **Alert Service** -> Kafka (consume detections + events), PostgreSQL, notification client.
- **Control Service** -> Kafka (optional command log), PostgreSQL (audit), MQTT/WebSocket/ROS adapters.
- **Dashboard** -> API Gateway (REST + WebSocket).
- **Simulation** -> Kafka (produce telemetry and optionally consume commands).
- **Training/Drift** -> S3 or local dataset, inference metrics (from logs or Prometheus), training scripts (PyTorch/YOLO).

## Data Ownership

- **Assets**: Asset service owns CRUD and status; telemetry service reads for enrichment.
- **Telemetry**: Telemetry service owns ingestion and stored aggregates.
- **Detections**: Inference service produces; alert service consumes and owns alert state.
- **Audit**: Control service and API write; audit log store is append-only.
- **Users/RBAC**: API gateway or dedicated auth service; PostgreSQL.
