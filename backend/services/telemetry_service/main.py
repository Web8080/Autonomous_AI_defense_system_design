"""
Telemetry service: ingest from Kafka, aggregate, store, and serve queries.
Raw telemetry flows through Kafka; this service persists aggregates and exposes API.
"""
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import UUID

from fastapi import FastAPI, Depends, Query
from kafka import KafkaConsumer
import asyncpg
import json

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TELEMETRY_RAW_TOPIC = os.getenv("TELEMETRY_RAW_TOPIC", "telemetry.raw")

pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    if pool:
        await pool.close()


app = FastAPI(title="Telemetry Service", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "telemetry-service"}


@app.post("/ingest")
async def ingest_one(
    body: dict,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Accept a single telemetry point (e.g. from gateway or test). Also used to push to Kafka in production."""
    asset_id = body.get("asset_id")
    timestamp = body.get("timestamp")
    source = body.get("source", "api")
    payload = body.get("payload", {})
    if not asset_id or not timestamp:
        return {"ok": False, "error": "asset_id and timestamp required"}
    # Bucket by minute for aggregation
    ts = timestamp[:16] if len(timestamp) >= 16 else timestamp
    await db.execute(
        """
        INSERT INTO telemetry.aggregated (asset_id, bucket_ts, source, count_events, payload_sample)
        VALUES ($1::uuid, $2::timestamptz, $3, 1, $4::jsonb)
        ON CONFLICT DO NOTHING
        """,
        asset_id,
        ts,
        source,
        json.dumps(payload),
    )
    # TODO: also produce to Kafka for inference pipeline
    return {"ok": True, "asset_id": asset_id, "timestamp": timestamp}


@app.get("/aggregated")
async def get_aggregated(
    asset_id: str | None = None,
    region_id: str | None = None,
    from_ts: str | None = Query(None),
    to_ts: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Return aggregated telemetry. Optional filter by asset_id or region (via assets join)."""
    conditions = ["1=1"]
    args = []
    idx = 1
    if asset_id:
        conditions.append(f"t.asset_id = ${idx}::uuid")
        args.append(asset_id)
        idx += 1
    if from_ts:
        conditions.append(f"t.bucket_ts >= ${idx}::timestamptz")
        args.append(from_ts)
        idx += 1
    if to_ts:
        conditions.append(f"t.bucket_ts <= ${idx}::timestamptz")
        args.append(to_ts)
        idx += 1
    if region_id:
        conditions.append(f"a.region_id = ${idx}")
        args.append(region_id)
        idx += 1
    args.append(limit)
    where = " AND ".join(conditions)
    join = "LEFT JOIN assets.assets a ON a.id = t.asset_id" if region_id else ""
    q = f"""
        SELECT t.id, t.asset_id, t.bucket_ts, t.source, t.count_events, t.payload_sample, t.created_at
        FROM telemetry.aggregated t {join}
        WHERE {where}
        ORDER BY t.bucket_ts DESC
        LIMIT ${idx}
    """
    rows = await db.fetch(q, *args)
    items = [
        {
            "id": r["id"],
            "asset_id": str(r["asset_id"]),
            "bucket_ts": r["bucket_ts"].isoformat(),
            "source": r["source"],
            "count_events": r["count_events"],
            "payload_sample": r["payload_sample"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}
