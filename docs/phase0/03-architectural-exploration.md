# Architectural Exploration: Autonomous AI Defense System

## Problem Statement

Build a secure, autonomous surveillance and response system for critical infrastructure. It must ingest multi-sensor data, run AI threat detection, control drones and ground vehicles within safety bounds, and give operators full visibility and override. The system must support local simulation and emulation, AI training/drift management, and optional cloud deployment.

---

## Architecture 1: Monolithic API with Embedded AI

### Components and Responsibilities

- **Single service**: REST API, WebSocket server, AI inference (YOLO), telemetry writer, alert engine, and job runner (Celery-like) in one process.
- **PostgreSQL**: All persistent state (assets, users, telemetry aggregates, alerts, audit).
- **Redis**: Session cache, rate limits, job queue.
- **File/volume**: Model weights and telemetry files on disk.

### Data Flow

- Sensors push to API; API writes to DB and optionally to a queue for async inference.
- Read path: API reads from DB and Redis; inference reads frames from memory or disk.
- Write path: single DB; no event bus.

### Scaling Characteristics

- Vertical scaling only for the monolith; inference and API compete for CPU/GPU.
- Bottlenecks: one process for inference and API; GPU cannot scale out.
- Scaling limits: one machine per deployment unless manually sharded by region.

### Failure Modes

- Process crash loses in-flight requests and any in-memory queue.
- DB or Redis down makes entire system unavailable.
- No partial degradation: inference and API fail together.

### Operational Burden

- Single deployment unit; simpler than many services.
- Debugging: one codebase, one log stream.
- GPU and CPU in same box; resource tuning is critical.
- Hard to upgrade inference independently.

### Tradeoffs

- **Gains:** Simple deployment, no event broker, low operational surface.
- **Losses:** No independent scaling of AI vs API, single point of failure, tight coupling.
- **Harder later:** Splitting out inference or telemetry to scale.
- **Impossible later:** Multi-region active-active without re-architecting.

### Liability Points

- Becomes a liability when throughput or inference load grows, or when different teams own API vs ML.

---

## Architecture 2: Event-Driven Microservices

### Components and Responsibilities

- **API gateway / BFF**: Auth, RBAC, REST and WebSocket; delegates to services.
- **Asset service**: CRUD and status for drones, vehicles, sensors.
- **Telemetry service**: Ingest (Kafka), aggregate, store (PostgreSQL/TimeSeries), serve queries and streams.
- **Inference service**: Consumes frames from Kafka or HTTP; runs YOLO; publishes detections to Kafka.
- **Alert service**: Consumes detections and rules; produces alerts; persists and notifies.
- **Control service**: Command issuance, emergency stop, heartbeat; talks to MQTT/WebSocket/ROS adapters.
- **Simulation service**: Simulated dynamics and sensor feeds; publishes to same Kafka topics.
- **Kafka**: Event backbone for telemetry, detections, alerts, commands.
- **PostgreSQL**: Assets, users, RBAC, audit, alert state.
- **Redis**: Sessions, rate limit, Celery broker.

### Data Flow

- Telemetry: sensors -> Kafka -> telemetry service (persist/aggregate) and inference service (consume frames).
- Detections: inference -> Kafka -> alert service and dashboard (via WS or poll).
- Commands: dashboard/API -> control service -> Kafka or MQTT -> assets (real or sim).
- Read path: dashboard and API query services via REST; real-time via WebSocket or Server-Sent Events fed from Kafka consumers.

### Scaling Characteristics

- Inference service scales by replica; each consumes from Kafka partitions.
- Telemetry and API scale independently.
- Bottlenecks: Kafka partition count, DB write throughput, single control path for emergency stop.

### Failure Modes

- Inference down: telemetry still stored; detections stop until recovery; operators can still override.
- Kafka down: ingestion and inference pause; replay after recovery.
- Control service down: emergency stop must be available via separate path (e.g. dedicated endpoint and heartbeat).

### Operational Burden

- Multiple services, Kafka cluster, monitoring per service.
- Deployment: Kubernetes/Helm or ECS; more complex than monolith.
- Debugging: trace IDs across Kafka and services.

### Tradeoffs

- **Gains:** Independent scaling, clear boundaries, replay and backfill via Kafka, multiple consumers for same events.
- **Losses:** Operational and deployment complexity, eventual consistency, need for idempotency and dead-letter handling.
- **Harder later:** Changing event schemas and upgrading consumers.
- **Impossible later:** Strong global consistency across all services without additional patterns.

### Liability Points

- Becomes a liability with very small teams or strict real-time guarantees that conflict with event latency.

---

## Architecture 3: Hybrid – Modular Monolith with Event Bus and Isolated Inference

### Components and Responsibilities

- **Core app**: FastAPI monolith for assets, telemetry API, alerts, control, RBAC, audit; publishes/consumes Kafka for telemetry and detections only.
- **Inference workers**: Separate deployable service(s); consume frames from Kafka or HTTP; publish detections to Kafka; stateless, GPU-capable.
- **Kafka**: Telemetry stream and detection stream; optional command log.
- **PostgreSQL**: All persistent state in one DB with clear schema boundaries.
- **Redis**: Session, Celery, rate limit.
- **Simulation**: Separate process or container; publishes synthetic telemetry to Kafka.

### Data Flow

- Telemetry: sensors -> Kafka -> core (write to DB, optional forward) and inference workers (consume, infer, publish detections).
- Detections: Kafka -> core (alerts, dashboard feed).
- Commands: dashboard -> core API -> MQTT/WebSocket/ROS (no Kafka in critical path for emergency stop).

### Scaling Characteristics

- Inference workers scale out; core app scales out for API/ingestion.
- Kafka partitions drive inference parallelism.
- Single DB; connection pool and write throughput are limits.

### Failure Modes

- Inference down: no new detections; core and control still work; emergency stop in core.
- Kafka down: ingestion and inference pause; core can still serve last-known state and accept overrides if commands are not Kafka-mediated.
- Core down: full outage for API and control; emergency stop must be designed to survive (e.g. direct MQTT to assets).

### Operational Burden

- Fewer services than full microservices; Kafka and inference still add ops.
- Clear split: “core” vs “inference” for ownership and scaling.

### Tradeoffs

- **Gains:** Independent scaling of inference, event replay for training and backfill, simpler than full microservices.
- **Losses:** Monolith can grow; DB shared by all domains.
- **Harder later:** Splitting core into more services if domains diverge.
- **Impossible later:** Not applicable if boundaries are kept clear.

### Liability Points

- Becomes a liability if core monolith becomes too large or teams need full service ownership per domain.

---

## Comparison Summary

| Aspect | Monolithic | Event-Driven Microservices | Hybrid |
|--------|------------|----------------------------|--------|
| Complexity | Low | High | Medium |
| Scalability | Low | High | Medium–High |
| Operational Overhead | Low | High | Medium |
| Development Speed | High initially | Slower (contracts, events) | Medium |
| Failure Resilience | Low | High (if emergency stop isolated) | Medium–High |
| AI/API independence | No | Yes | Yes |

---

## Recommendation

**Chosen Approach:** Architecture 2 (Event-Driven Microservices), with emergency stop and critical control paths kept out of the event bus where possible (direct API -> control service -> MQTT/adapters).

**Justification:**

- Independent scaling of inference vs API and telemetry is required for latency and throughput NFRs.
- Multi-tenant operators and regions align with service boundaries (asset, telemetry, alert, control).
- Audit, fail-safes, and overrides need a clear control plane; keeping emergency stop in a dedicated control service with minimal dependencies reduces risk.
- Simulation and real hardware can both feed the same Kafka topics, simplifying local testing and emulation.
- Training and drift pipelines can consume from the same telemetry and detection streams without touching the main API.

**Acknowledged Tradeoffs:**

- Higher operational cost (Kafka, multiple services, tracing).
- Event schema evolution and consumer versioning must be managed.
- Emergency stop and heartbeat must be designed so they do not depend on Kafka availability.

**Migration Triggers:**

- Reconsider if team size or budget cannot support microservices; then Hybrid is the fallback.
- Reconsider if strong consistency across all domains becomes a hard requirement; then add explicit consistency patterns per domain.
