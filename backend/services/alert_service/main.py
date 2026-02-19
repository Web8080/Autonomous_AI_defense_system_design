"""
Alert service: consume detections/events from Kafka, apply rules, persist alerts, notify.
Scaffold: REST CRUD and stub for Kafka consumer (run in worker).
"""
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import UUID

from fastapi import FastAPI, Depends, Query
import asyncpg

from defense_shared.schemas import AlertCreate, AlertSeverity, AlertState

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")

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


app = FastAPI(title="Alert Service", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "alert-service"}


@app.get("/alerts")
async def list_alerts(
    region_id: str | None = None,
    state: str | None = None,
    severity: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    conditions = ["1=1"]
    args = []
    idx = 1
    if region_id:
        conditions.append(f"region_id = ${idx}")
        args.append(region_id)
        idx += 1
    if state:
        conditions.append(f"state = ${idx}")
        args.append(state)
        idx += 1
    if severity:
        conditions.append(f"severity = ${idx}")
        args.append(severity)
        idx += 1
    args.append(limit)
    where = " AND ".join(conditions)
    q = f"""
        SELECT id, source, severity, title, body, asset_id, region_id, detection_id, state,
               metadata, acknowledged_by, acknowledged_at, created_at, updated_at
        FROM alerts.alerts
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${idx}
    """
    rows = await db.fetch(q, *args)
    items = [
        {
            "id": str(r["id"]),
            "source": r["source"],
            "severity": r["severity"],
            "title": r["title"],
            "body": r["body"],
            "asset_id": str(r["asset_id"]) if r["asset_id"] else None,
            "region_id": r["region_id"],
            "detection_id": r["detection_id"],
            "state": r["state"],
            "metadata": r["metadata"] or {},
            "acknowledged_by": str(r["acknowledged_by"]) if r["acknowledged_by"] else None,
            "acknowledged_at": r["acknowledged_at"].isoformat() if r["acknowledged_at"] else None,
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


@app.post("/alerts", status_code=201)
async def create_alert(
    body: AlertCreate,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    row = await db.fetchrow(
        """
        INSERT INTO alerts.alerts (source, severity, title, body, asset_id, region_id, detection_id, metadata)
        VALUES ($1, $2, $3, $4, $5::uuid, $6, $7, $8::jsonb)
        RETURNING id, source, severity, title, body, asset_id, region_id, detection_id, state, created_at
        """,
        body.source,
        body.severity.value,
        body.title,
        body.body,
        body.asset_id,
        body.region_id,
        body.detection_id,
        body.metadata,
    )
    return {
        "id": str(row["id"]),
        "source": row["source"],
        "severity": row["severity"],
        "title": row["title"],
        "body": row["body"],
        "asset_id": str(row["asset_id"]) if row["asset_id"] else None,
        "region_id": row["region_id"],
        "detection_id": row["detection_id"],
        "state": row["state"],
        "created_at": row["created_at"].isoformat(),
    }


@app.patch("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: UUID,
    body: dict,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    user_id = body.get("user_id")
    row = await db.fetchrow(
        """
        UPDATE alerts.alerts
        SET state = 'acknowledged', acknowledged_by = $1::uuid, acknowledged_at = NOW(), updated_at = NOW()
        WHERE id = $2
        RETURNING id, state, acknowledged_at
        """,
        user_id,
        alert_id,
    )
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"id": str(row["id"]), "state": row["state"], "acknowledged_at": row["acknowledged_at"].isoformat()}
