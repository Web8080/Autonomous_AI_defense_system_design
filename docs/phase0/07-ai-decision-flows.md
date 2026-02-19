# AI Decision Flows and Operator Workflows

## AI Decision Flow (System AI)

1. Ingest: Frames and telemetry arrive via Kafka (inference.frames, telemetry.raw).
2. Infer: Inference service runs YOLO (or stub), produces detections with class, confidence, threat_score.
3. Publish: Detections go to inference.detections.
4. Alert: Alert service consumes detections; if threat_score above threshold, create alert and notify.
5. Decide: Autonomous agent (optional consumer) reads detections; if threat_score above agent threshold and within geofence, plans action (investigate, patrol, retreat).
6. Execute: Planned command sent to control topic or MQTT; control service logs and forwards to assets.
7. Constraint: No lethal or irreversible action; geofence and max speed enforced in agent and control service.

## Operator Workflow (Super Admin)

1. Log in; RBAC grants full access.
2. View map: all assets, all regions, threat overlays, alerts.
3. Issue emergency stop (all or per asset); action logged.
4. Override: take control of asset; command logged with issued_by.
5. Admin: manage users, roles, regions; configure alert rules and fail-safes; approve model rollback.

## Operator Workflow (Local Operator)

1. Log in; RBAC grants access only to assigned region_ids.
2. View map: assets and alerts filtered by region.
3. Acknowledge or escalate alerts.
4. Emergency stop for assets in assigned region only.
5. Manual override for assets in region.

## Fail-Safe Flow

1. Emergency stop requested from dashboard or API.
2. API gateway validates JWT and role; proxies to control service.
3. Control service writes to audit.command_log; sends command to MQTT/asset adapter.
4. Asset (real or sim) receives command and halts motion.
5. If heartbeat is used: control service sends periodic heartbeat; asset enters safe state if heartbeat is lost.
