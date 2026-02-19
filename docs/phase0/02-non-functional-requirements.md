# Non-Functional Requirements

## Latency

| Metric | Target | Notes |
|--------|--------|------|
| AI inference (single frame) | p95 < 200 ms | Per-camera pipeline |
| Telemetry ingestion to storage | p99 < 500 ms | From edge to queryable |
| Alert delivery to operator UI | p99 < 2 s | From event to dashboard |
| Emergency stop propagation | < 1 s | From button to asset |
| Dashboard real-time update | < 3 s | Map and telemetry refresh |

## Throughput

| Metric | Target | Notes |
|--------|--------|------|
| Telemetry events | 10k msg/s aggregate | Kafka ingestion |
| Inference requests | 100 req/s per inference service | Horizontally scalable |
| Concurrent dashboard users | 50 per deployment | Session and WS limits |

## Availability

| Metric | Target | Notes |
|--------|--------|------|
| Core API and dashboard | 99.5% uptime | Excluding planned maintenance |
| Telemetry pipeline | 99.9% | At-least-once delivery |
| AI inference | 99% | Degrade to cached last result if needed |
| Emergency stop path | 99.99% | Isolated, minimal dependencies |

## Security

| Requirement | Description |
|-------------|-------------|
| Authentication | JWT with 7-day expiry; refresh tokens; no long-lived API keys in frontend |
| Authorization | RBAC enforced server-side on every request; region-scoped for Local Operator |
| Secrets | No secrets in code or logs; .env and secret manager only |
| Network | TLS everywhere; CORS whitelist; rate limiting on auth and public endpoints |
| Audit | All privileged actions and overrides logged with principal and timestamp |
| Input | Sanitize and validate all inputs; parameterized queries; no raw SQL from user input |

## Compliance and Operations

| Area | Requirement |
|------|-------------|
| Data retention | Configurable retention for telemetry and audit logs; support for deletion policies |
| Logging | Structured logs; no PII in logs; log levels configurable |
| Deployment | No manual production changes; CI/CD with approval gates for prod |
| Rollback | One-command rollback for services and model versions |

## Scalability and Resilience

| Area | Requirement |
|------|-------------|
| Horizontal scaling | Stateless services; scale inference and API by replica count |
| Failure isolation | Failure of one region or inference node does not take down emergency stop |
| Backpressure | Telemetry and inference queues support backpressure; drop or sample when overloaded |
| Idempotency | Critical commands (e.g. emergency stop) idempotent by id where applicable |
