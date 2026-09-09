"""
Background worker: consume telemetry.raw from the drone bridge and write
vehicle state rows into telemetry.vehicle_state (TimescaleDB hypertable).

Run as a separate process or Celery task. Uses same DB and Kafka env vars as
the main app.

The drone bridge publishes whole VehicleState snapshots (see
drone_bridge/connection.py), so this consumer is deliberately tightly coupled
to that shape: timestamp, position, battery, GPS, flight mode, link health.
"""
import os
import json
import asyncio
from datetime import datetime, timezone

import asyncpg
from kafka import KafkaConsumer


INSERT_SQL = """
INSERT INTO telemetry.vehicle_state
    (asset_id, org_id, flight_id, ts, position, altitude_rel_m, altitude_amsl_m,
     heading_deg, groundspeed_ms, vertical_speed_ms, roll_deg, pitch_deg, yaw_deg,
     battery_pct, battery_voltage, gps_fix_type, gps_satellites, flight_mode,
     armed, rssi_dbm, link_latency_ms)
VALUES
    ($1, $2, $3, $4,
     CASE WHEN $5::numeric IS NOT NULL AND $6::numeric IS NOT NULL
          THEN ST_SetSRID(ST_MakePoint($6, $5), 4326)::geography
          ELSE NULL END,
     $7, $8, $9, $10, $11, $12, $13, $14,
     $15, $16, $17, $18, $19, $20, $21, $22)
ON CONFLICT (asset_id, ts) DO UPDATE SET
    flight_mode = EXCLUDED.flight_mode,
    armed = EXCLUDED.armed,
    battery_pct = EXCLUDED.battery_pct,
    position = EXCLUDED.position,
    altitude_rel_m = EXCLUDED.altitude_rel_m,
    groundspeed_ms = EXCLUDED.groundspeed_ms
"""


def run():
    consumer = KafkaConsumer(
        TELEMETRY_RAW_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        group_id="telemetry-vehicle-state",
        auto_offset_reset="earliest",
    )
    loop = asyncio.get_event_loop()
    pool = loop.run_until_complete(asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=8))
    org_cache: dict[str, str | None] = {}
    try:
        for msg in consumer:
            try:
                payload = json.loads(msg.value.decode())
                _persist(pool, loop, payload, org_cache)
            except Exception as exc:  # noqa: BLE001 - keep the consumer alive
                print("telemetry consumer error:", exc)
    finally:
        loop.run_until_complete(pool.close())


def _resolve_org(pool, loop, asset_id: str, cache: dict) -> str | None:
    if asset_id in cache:
        return cache[asset_id]
    row = loop.run_until_complete(
        pool.fetchrow("SELECT org_id FROM assets.assets WHERE id = $1::uuid", asset_id)
    )
    org = str(row["org_id"]) if row and row["org_id"] else None
    cache[asset_id] = org
    return org


def _persist(pool, loop, p: dict, org_cache: dict | None = None) -> None:
    asset_id = p.get("asset_id")
    if not asset_id:
        return
    if isinstance(asset_id, str):
        # MAVSDK asset ids are the platform's UUIDs; the bridge already validates.
        pass
    ts = p.get("timestamp") or datetime.now(timezone.utc).isoformat()

    org_id = p.get("org_id")
    if org_id is None and org_cache is not None:
        org_id = _resolve_org(pool, loop, asset_id, org_cache)

    loop.run_until_complete(
        pool.execute(
            INSERT_SQL,
            asset_id,
            p.get("org_id"),
            p.get("flight_id"),
            ts,
            p.get("latitude"),
            p.get("longitude"),
            p.get("altitude_rel_m"),
            p.get("altitude_amsl_m"),
            p.get("heading_deg"),
            p.get("groundspeed_ms"),
            p.get("vertical_speed_ms"),
            p.get("roll_deg"),
            p.get("pitch_deg"),
            p.get("yaw_deg"),
            p.get("battery_pct"),
            p.get("battery_voltage"),
            p.get("gps_fix_type"),
            p.get("gps_satellites"),
            p.get("flight_mode"),
            p.get("armed"),
            p.get("rssi_dbm"),
            p.get("link_latency_ms"),
        )
    )


if __name__ == "__main__":
    run()