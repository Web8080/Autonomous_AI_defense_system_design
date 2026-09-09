"""
Background worker: persist detections.detections from the inference service.

This is the training-data capture path: operator-reviewed detections (with
`review_state` = confirmed/corrected) feed the next training run's labels. For
that to be true the rows here must be trustworthy, so this consumer is strict:

- Detections without model provenance (model_id/model_version) are skipped and
  counted, never silently persisted. Unprovable detections cannot be labels.
- Stub/backfill detections (metadata.provenance == "stub") are skipped: the
  fake confidences from the pre-model inference path would pollute active
  learning.

Consumes `inference.detections`; mirrors the telemetry-consumer pattern.
"""
import os
import json
import asyncio

import asyncpg
from kafka import KafkaConsumer

INFERENCE_DETECTIONS_TOPIC = os.getenv("INFERENCE_DETECTIONS_TOPIC", "inference.detections")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")

INSERT_SQL = """
INSERT INTO detections.detections
    (org_id, site_id, asset_id, flight_id, detected_at, frame_id, class_name,
     confidence, bbox, altitude_m, model_id, model_version, zone_id,
     in_restricted_zone, metadata)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::numeric[], $10, $11, $12, $13, $14, $15::jsonb)
"""


class _Counters:
    def __init__(self):
        self.persisted = 0
        self.skipped_no_provenance = 0
        self.skipped_stub = 0
        self.skipped_no_org = 0
        self.errors = 0

    def line(self) -> str:
        return (
            f"detections consumer: persisted={self.persisted} "
            f"skip_no_prov={self.skipped_no_provenance} skip_stub={self.skipped_stub} "
            f"skip_no_org={self.skipped_no_org} errors={self.errors}"
        )


COUNTERS = _Counters()
org_cache: dict[str, str | None] = {}


def _resolve_org(pool, loop, asset_id: str) -> str | None:
    if asset_id in org_cache:
        return org_cache[asset_id]
    row = loop.run_until_complete(
        pool.fetchrow("SELECT org_id FROM assets.assets WHERE id = $1::uuid", asset_id)
    )
    org = str(row["org_id"]) if row and row["org_id"] else None
    org_cache[asset_id] = org
    return org


def _persist(pool, loop, det: dict) -> None:
    if det.get("metadata") and det["metadata"].get("provenance") == "stub":
        COUNTERS.skipped_stub += 1
        return
    model_version = det.get("model_version")
    if not model_version:
        COUNTERS.skipped_no_provenance += 1
        return

    asset_id = det.get("asset_id")
    if not asset_id:
        COUNTERS.skipped_no_provenance += 1
        return
    org_id = det.get("org_id") or _resolve_org(pool, loop, asset_id)
    if not org_id:
        COUNTERS.skipped_no_org += 1
        return

    try:
        loop.run_until_complete(
            pool.execute(
                INSERT_SQL,
                org_id,
                det.get("site_id"),
                asset_id,
                det.get("flight_id"),
                det.get("timestamp") or det.get("detected_at"),
                det.get("frame_id"),
                det["class_name"],
                det.get("confidence"),
                det.get("bbox"),
                det.get("altitude_m"),
                det.get("model_id"),
                model_version,
                det.get("zone_id"),
                bool(det.get("in_restricted_zone", False)),
                json.dumps({k: v for k, v in (det.get("metadata") or {}).items() if k != "provenance"}),
            )
        )
        COUNTERS.persisted += 1
    except Exception as exc:  # noqa: BLE001 - keep the consumer alive
        COUNTERS.errors += 1
        print("detections persist error:", exc, "frame=", det.get("frame_id"))


def run():
    if not KAFKA_BOOTSTRAP:
        return
    consumer = KafkaConsumer(
        INFERENCE_DETECTIONS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        group_id="detections-persist",
        auto_offset_reset="earliest",
    )
    loop = asyncio.get_event_loop()
    pool = loop.run_until_complete(asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=8))
    try:
        for msg in consumer:
            try:
                payload = json.loads(msg.value.decode())
                dets = payload if isinstance(payload, list) else payload.get("detections", [payload])
                for det in dets:
                    if isinstance(det, dict):
                        _persist(pool, loop, det)
                if COUNTERS.persisted % 100 == 0 and COUNTERS.persisted > 0:
                    print(COUNTERS.line())
            except Exception as exc:  # noqa: BLE001
                COUNTERS.errors += 1
                print("detections consumer error:", exc)
    finally:
        loop.run_until_complete(pool.close())


if __name__ == "__main__":
    run()